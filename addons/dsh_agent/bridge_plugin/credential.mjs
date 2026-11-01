import { spawn } from 'node:child_process';
import { lstat, open, realpath } from 'node:fs/promises';
import { isAbsolute, join, parse, resolve } from 'node:path';

export const TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,256}$/;
const FORMAT = 'alice-dsh-bridge-credential-v1';
const PROTECTION = 'dpapi-current-user';
const MAX_FILE_BYTES = 8192;
const MAX_CIPHERTEXT_BYTES = 4096;
const DECRYPT_TIMEOUT_MS = 5000;

// Fixed code only. Ciphertext enters stdin; the token never enters command
// arguments, the child environment, a file, or a diagnostic message.
const UNPROTECT_SCRIPT = String.raw`
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
  Add-Type -AssemblyName System.Security
  $encrypted = [Convert]::FromBase64String([Console]::In.ReadToEnd())
  if ($encrypted.Length -eq 0 -or $encrypted.Length -gt 4096) { exit 1 }
  $entropy = [Text.Encoding]::UTF8.GetBytes('Alice.DSH.Bridge.Credential.v1')
  $plain = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
  try {
    if ($plain.Length -lt 1 -or $plain.Length -gt 1024) { exit 1 }
    foreach ($value in $plain) {
      if ($value -gt 127) { exit 1 }
    }
    [Console]::Out.Write([Text.Encoding]::ASCII.GetString($plain))
  } finally {
    if ($null -ne $plain) { [Array]::Clear($plain, 0, $plain.Length) }
  }
} catch { exit 1 }
`;

function samePath(left, right) {
  return resolve(left).toLowerCase() === resolve(right).toLowerCase();
}

function fingerprint(info) {
  return [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs, info.nlink].join(':');
}

function localPath() {
  if (process.platform !== 'win32') return null;
  const base = process.env.LOCALAPPDATA;
  if (typeof base !== 'string' || !/^[A-Za-z]:[\\/]/.test(base)
      || !isAbsolute(base) || /[\u0000-\u001f]/.test(base)) {
    throw new Error('credential_path_unavailable');
  }
  return join(resolve(base), 'Alice', 'dsh-bridge', 'credential.json');
}

async function validateParents(file) {
  const directory = parse(file).dir;
  const root = parse(directory).root;
  const parts = directory.slice(root.length).split(/[\\/]+/).filter(Boolean);
  let current = root;
  for (const part of parts) {
    current = join(current, part);
    const info = await lstat(current, { bigint: true });
    if (!info.isDirectory() || info.isSymbolicLink() || !samePath(await realpath(current), current)) {
      throw new Error('credential_path_invalid');
    }
  }
}

export async function inspectManagedCredential() {
  let file;
  try {
    file = localPath();
    if (file === null) return { kind: 'unsupported', fingerprint: 'unsupported' };
    await validateParents(file);
    const info = await lstat(file, { bigint: true });
    const id = fingerprint(info);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1n
        || info.size < 1n || info.size > BigInt(MAX_FILE_BYTES)
        || !samePath(await realpath(file), file)) {
      return { kind: 'invalid', fingerprint: `invalid:${id}` };
    }
    return { kind: 'present', fingerprint: id, file };
  } catch (error) {
    if (error?.code === 'ENOENT') return { kind: 'missing', fingerprint: 'missing' };
    return { kind: 'invalid', fingerprint: 'path-unavailable' };
  }
}

function strictStrings(bytes, names) {
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  // The formats contain ASCII string fields. Reject duplicates, escapes,
  // extra fields and non-string JSON without relying on JSON.parse's last-key wins.
  const body = /^\s*\{([\s\S]*)\}\s*$/.exec(text)?.[1];
  if (body === undefined) throw new Error('credential_invalid');
  const fields = body.split(',');
  if (fields.length !== names.length) throw new Error('credential_invalid');
  const values = new Map();
  for (const field of fields) {
    const match = /^\s*"([a-z_]+)"\s*:\s*"([A-Za-z0-9+/_=-]+)"\s*$/.exec(field);
    if (!match || !names.includes(match[1]) || values.has(match[1])) throw new Error('credential_invalid');
    values.set(match[1], match[2]);
  }
  return values;
}

function parseEnvelope(bytes) {
  const values = strictStrings(bytes, ['format', 'protection', 'credential_id', 'ciphertext']);
  if (values.get('format') !== FORMAT || values.get('protection') !== PROTECTION
      || !/^[a-f0-9]{64}$/.test(values.get('credential_id') ?? '')) throw new Error('credential_invalid');
  const ciphertext = values.get('ciphertext');
  if (typeof ciphertext !== 'string' || ciphertext.length > 5464
      || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(ciphertext)) {
    throw new Error('credential_invalid');
  }
  const decoded = Buffer.from(ciphertext, 'base64');
  if (!decoded.length || decoded.length > MAX_CIPHERTEXT_BYTES || decoded.toString('base64') !== ciphertext) {
    throw new Error('credential_invalid');
  }
  return { ciphertext, credentialId: values.get('credential_id') };
}

async function encryptedEnvelope(snapshot) {
  if (snapshot.kind !== 'present') throw new Error('credential_unavailable');
  await validateParents(snapshot.file);
  const handle = await open(snapshot.file, 'r');
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.nlink !== 1n || fingerprint(before) !== snapshot.fingerprint) {
      throw new Error('credential_changed');
    }
    const buffer = Buffer.alloc(MAX_FILE_BYTES + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    const after = await handle.stat({ bigint: true });
    const latest = await inspectManagedCredential();
    if (bytesRead < 1 || bytesRead > MAX_FILE_BYTES || BigInt(bytesRead) !== before.size
        || fingerprint(after) !== snapshot.fingerprint || latest.kind !== 'present'
        || latest.fingerprint !== snapshot.fingerprint) throw new Error('credential_changed');
    return parseEnvelope(buffer.subarray(0, bytesRead));
  } finally { await handle.close(); }
}

function unprotect({ ciphertext, credentialId }, signal) {
  return new Promise((resolveToken, reject) => {
    const systemRoot = process.env.SystemRoot ?? process.env.SYSTEMROOT;
    if (process.platform !== 'win32' || typeof systemRoot !== 'string' || !isAbsolute(systemRoot)
        || /[\u0000-\u001f]/.test(systemRoot) || signal?.aborted) {
      reject(new Error('credential_unavailable'));
      return;
    }
    const executable = join(resolve(systemRoot), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
    let child;
    let timer;
    let settled = false;
    let output = Buffer.alloc(0);
    const finish = (valid) => {
      if (settled) return;
      settled = true;
      if (!valid) {
        try { child?.kill(); } catch { /* The failed helper is already unavailable. */ }
      }
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      let token;
      if (valid) {
        try {
          const values = strictStrings(output, ['format', 'credential_id', 'token']);
          if (values.get('format') === 'alice-dsh-bridge-secret-v1'
              && values.get('credential_id') === credentialId
              && /^[A-Za-z0-9_-]{43}$/.test(values.get('token') ?? '')) token = values.get('token');
        } catch { /* Invalid protected plaintext is not a credential. */ }
      }
      output.fill(0);
      output = Buffer.alloc(0);
      if (typeof token === 'string') resolveToken(token);
      else reject(new Error('credential_unavailable'));
    };
    const abort = () => { child?.kill(); finish(false); };
    try {
      child = spawn(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', UNPROTECT_SCRIPT], {
        windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'ignore'],
        // In particular, never inherit an existing manual bridge token.
        env: { SystemRoot: systemRoot, WINDIR: systemRoot },
      });
      child.on('error', () => finish(false));
      child.stdin.on('error', () => finish(false));
      child.stdout.on('error', () => finish(false));
      child.stdout.on('data', (chunk) => {
        if (settled) return;
        if (output.length + chunk.length > 1024 || [...chunk].some((value) => value > 127)) {
          abort();
          return;
        }
        const previous = output;
        output = Buffer.concat([previous, chunk]);
        previous.fill(0);
      });
      child.once('close', (code) => finish(code === 0));
      signal?.addEventListener('abort', abort, { once: true });
      timer = setTimeout(abort, DECRYPT_TIMEOUT_MS);
      timer.unref();
      if (signal?.aborted) abort();
      else child.stdin.end(ciphertext, 'ascii');
    } catch { abort(); }
  });
}

export async function readManagedToken(snapshot, signal) {
  const envelope = await encryptedEnvelope(snapshot);
  const token = await unprotect(envelope, signal);
  const latest = await inspectManagedCredential();
  if (signal?.aborted || latest.kind !== 'present' || latest.fingerprint !== snapshot.fingerprint) {
    throw new Error('credential_changed');
  }
  return token;
}
