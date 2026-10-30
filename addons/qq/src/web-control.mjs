// Public local connector process. stdin/stdout are a bounded JSONL protocol;
// platform secrets never appear in events, console output, URLs or arguments.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
import { createRequire } from 'node:module';
import { QQBot } from '@tencent-connect/qqbot-nodejs';
import { loadConfig, readSecret } from './config.mjs';
import { Ledger, SafeLog, acquireLock, unlockStale, atomicJSON, hash, privateDirectory } from './storage.mjs';
import { AliceClient } from './alice-client.mjs';
import { Bridge } from './bridge.mjs';

const require = createRequire(import.meta.url);
const QRCode = require('qrcode-terminal/vendor/QRCode');
const QRErrorCorrectLevel = require('qrcode-terminal/vendor/QRCode/QRErrorCorrectLevel');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const args = process.argv.slice(2);
const [nodeMajor, nodeMinor] = process.versions.node.split('.').map(Number);
const supportedNode = nodeMajor > 22 || (nodeMajor === 22 && nodeMinor >= 19);
const validID = value => typeof value === 'string' && /^[A-Za-z0-9_-]{16,128}$/.test(value);
const validIdentity = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const validSecret = value => typeof value === 'string' && /^[\x21-\x7e]{16,4096}$/.test(value);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let file, aliceURL;
try {
  if (args.length !== 4 || args[0] !== '--config' || args[2] !== '--alice-url') throw new Error();
  file = path.resolve(args[1]);
  const url = new URL(args[3]);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error();
  aliceURL = url.origin;
} catch { process.exit(2); }

let current = { state: 'idle', operation_id: '' };
let operation = null;
let pending = null;
let awaitingBinding = null;
let running = null;
let closing = false;
let inputBuffer = '';
let commandChain = Promise.resolve();

function emit(value) {
  if (!closing && !process.stdout.destroyed) process.stdout.write(JSON.stringify(value) + '\n');
}
function state(next, extra = {}, id = current.operation_id) {
  current = { state: next, operation_id: id, ...extra };
  emit({ type: 'state', ...current });
}
function error(code, id = current.operation_id) { state('error', { error_code: code }, id); }
function rawConfig() {
  if (fs.statSync(file).size > 16384) throw new Error('config_invalid');
  return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
}
function config(pairing = false, onboarding = false) {
  const c = loadConfig(file, { pairing, onboarding });
  c.alice.base_url = aliceURL; // Follow this Alice instance's actual loopback port.
  return c;
}
function token(c) {
  const value = readSecret(c.alice.machine_token_file);
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(value)) throw new Error('token_invalid');
  return value;
}
function runConfig() {
  if (!fs.existsSync(file)) throw new Error('not_configured');
  const c = config(true, true);
  if (!validIdentity(c.qq.app_id) || !fs.existsSync(c.qq.app_secret_file)) throw new Error('not_configured');
  if (!validIdentity(c.qq.owner_openid)) throw new Error('owner_pair_required');
  return config();
}
function initialize() {
  if (!fs.existsSync(file)) {
    privateDirectory(path.dirname(file));
    const raw = JSON.parse(fs.readFileSync(path.join(root, 'addons/qq/config.example.json'), 'utf8'));
    raw.alice.base_url = aliceURL;
    const fd = fs.openSync(file, 'wx', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify(raw)); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
  }
  const c = config(true, true);
  if (!fs.existsSync(c.alice.machine_token_file)) {
    privateDirectory(path.dirname(c.alice.machine_token_file));
    const fd = fs.openSync(c.alice.machine_token_file, 'wx', 0o600);
    try { fs.writeFileSync(fd, randomBytes(32).toString('base64url')); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
  }
  token(c);
  return c;
}
function qrMatrix(value) {
  if (typeof value !== 'string' || value.length > 4096) throw new Error('qr_invalid');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.hostname !== 'q.qq.com' || url.port || url.username || url.password ||
    url.hash || url.pathname !== '/qqbot/openclaw/connect.html') throw new Error('qr_invalid');
  const qr = new QRCode(-1, QRErrorCorrectLevel.L);
  qr.addData(value); qr.make();
  const n = qr.getModuleCount();
  if (n < 21 || n > 177) throw new Error('qr_invalid');
  return Array.from({ length: n }, (_, r) => Array.from({ length: n }, (_, c) => qr.isDark(r, c) ? '1' : '0').join(''));
}
function operationLive(op) { return operation === op && !op.controller.signal.aborted && !closing; }
function availableLock(c) {
  if (fs.existsSync(path.join(c.storage.state_dir, 'bridge.lock'))) {
    // The existing unlock utility removes only locks whose PID is proven gone.
    // A live/reused PID, denied process query or damaged lock always fails closed.
    try { unlockStale(c.storage.state_dir); }
    catch { throw new Error('external_bridge_or_stale_lock'); }
  }
  return acquireLock(c.storage.state_dir);
}
function finishOperation() {
  const op = operation;
  operation = null;
  if (!op) return;
  clearTimeout(op.timer);
  op.controller.abort();
  op.dispose?.();
  op.bot?.stop();
  op.release?.();
}
function beginOperation(id, c, keepPending = false) {
  if (running || awaitingBinding) throw new Error('busy');
  finishOperation();
  if (!keepPending) pending = null;
  const release = availableLock(c);
  const op = { id, c, release, controller: new AbortController(), expires: Date.now() + 300000 };
  operation = op;
  op.timer = setTimeout(() => {
    if (operation !== op) return;
    finishOperation(); pending = null;
    error('operation_expired', id);
  }, 300000);
  return op;
}
function makeBot(c, credential, disconnected = () => {}, log = null) {
  // SDK 1.0.4 has READY/RESUMED events but no disconnect event/getter. Its public
  // logger is a diagnostic callback; only fixed prefixes affect local state.
  // Never forward the SDK's raw strings (which can contain event data or URLs).
  const diagnostic = message => {
    if (typeof message === 'string' && (
      message.startsWith('[alice-qq-owner] WebSocket closed:') ||
      message.startsWith('[alice-qq-owner] Reconnecting in ') ||
      message.startsWith('[alice-qq-owner] Connection failed:') ||
      message.startsWith('[alice-qq-owner] WebSocket error:'))) disconnected();
  };
  return new QQBot({ appId: credential?.appId ?? c.qq.app_id,
    appSecret: credential?.appSecret ?? readSecret(c.qq.app_secret_file),
    accountId: 'alice-qq-owner', baseUrl: 'https://api.bot.qq.com', transport: 'websocket',
    tokenPrefetch: 'sync', intents: 1 << 25, markdownSupport: false,
    logger: { info: diagnostic, debug: diagnostic, warn: diagnostic,
      error: value => { diagnostic(value); try { log?.write('qq_sdk_error'); } catch { disconnected(); } } },
  });
}
async function scan(id) {
  const c = initialize();
  if (fs.existsSync(c.qq.app_secret_file)) throw new Error('already_configured');
  const op = beginOperation(id, c);
  state('scanning', { expires_at: op.expires }, id);
  try {
    const { startQrConnect } = await import('@tencent-connect/qqbot-connector');
    if (!operationLive(op)) return;
    op.dispose = startQrConnect({
      onQrDisplayed(url) {
        if (!operationLive(op) || op.scanComplete) return;
        try { state('scanning', { qr_matrix: qrMatrix(url), expires_at: op.expires }, id); }
        catch { finishOperation(); pending = null; error('qr_invalid', id); }
      },
      onQrExpired() {
        if (operationLive(op) && !op.scanComplete) state('scanning', { expires_at: op.expires, error_code: 'qr_expired' }, id);
      },
      onSuccess(values) {
        if (!operationLive(op) || op.scanComplete) return;
        if (!Array.isArray(values) || values.length !== 1 || !validIdentity(values[0]?.appId) || !validSecret(values[0]?.appSecret)) {
          finishOperation(); error('credentials_invalid', id); return;
        }
        const value = values[0];
        op.scanComplete = true;
        pending = { appId: value.appId, appSecret: value.appSecret, owner: validIdentity(value.userOpenid) ? value.userOpenid : '', fromScan: true };
        op.dispose?.();
        state('awaiting_confirmation', { app_id: pending.appId, ...(pending.owner ? { owner_openid: pending.owner } : { error_code: 'owner_pair_required' }), expires_at: op.expires }, id);
      },
      onFailure() {
        if (!operationLive(op) || op.scanComplete) return;
        finishOperation(); pending = null; error('scan_failed', id);
      },
    }, { displayQrCodeToConsole: false, source: '', signal: op.controller.signal });
  } catch {
    if (operationLive(op)) { finishOperation(); pending = null; error('scan_failed', id); }
  }
}
async function pair(id) {
  if (!fs.existsSync(file)) throw new Error('not_configured');
  const c = config(true, !!pending?.fromScan);
  if (!pending) {
    if (!validIdentity(c.qq.app_id)) throw new Error('not_configured');
    pending = { appId: c.qq.app_id, appSecret: readSecret(c.qq.app_secret_file), owner: '', fromScan: false };
  }
  const op = beginOperation(id, c, true);
  const code = randomBytes(10).toString('hex');
  const bot = makeBot(c, pending);
  op.bot = bot;
  state('pairing', { app_id: pending.appId, pair_code: `/pair ${code}`, expires_at: op.expires }, id);
  bot.on('message', (_ctx, msg) => {
    if (!operationLive(op) || current.state !== 'pairing' || msg.kind !== 'c2c' || msg.senderIsBot ||
      msg.rawEventType !== 'C2C_MESSAGE_CREATE' || msg.content?.trim() !== `/pair ${code}` ||
      msg.raw?.author?.user_openid !== msg.senderId || msg.raw?.id !== msg.messageId || !validIdentity(msg.senderId)) return;
    pending.owner = msg.senderId;
    bot.stop();
    state('awaiting_confirmation', { app_id: pending.appId, owner_openid: pending.owner, expires_at: op.expires }, id);
  });
  op.gateway = bot.start(op.controller.signal).catch(() => {
    if (operationLive(op) && current.state === 'pairing') { finishOperation(); pending = null; error('pair_failed', id); }
  });
}
function binding(c, id, action) {
  awaitingBinding = { id, action, c };
  emit({ type: 'binding', operation_id: id, app_id: c.qq.app_id,
    sender_id: c.qq.owner_openid, token_sha256: hash(token(c)) });
}
function confirm(id) {
  if (!operation || operation.id !== id || current.state !== 'awaiting_confirmation' || !pending?.owner || Date.now() >= operation.expires) throw new Error('confirmation_stale');
  const c = operation.c;
  const raw = rawConfig();
  if ((validIdentity(raw.qq.owner_openid) && raw.qq.owner_openid !== pending.owner) ||
    (validIdentity(raw.qq.app_id) && raw.qq.app_id !== pending.appId)) throw new Error('binding_conflict');
  // Existing credentials/bindings are never silently replaced. A fresh scan is
  // only allowed when no secret exists; re-pair requires this explicit confirm.
  let wroteSecret = false;
  try {
    if (pending.fromScan) {
      if (fs.existsSync(c.qq.app_secret_file)) throw new Error('already_configured');
      privateDirectory(path.dirname(c.qq.app_secret_file));
      const fd = fs.openSync(c.qq.app_secret_file, 'wx', 0o600);
      wroteSecret = true;
      try { fs.writeFileSync(fd, pending.appSecret); fs.fsyncSync(fd); }
      finally { fs.closeSync(fd); }
    }
    raw.qq.app_id = pending.appId; raw.qq.owner_openid = pending.owner;
    raw.alice.base_url = aliceURL;
    atomicJSON(file, raw);
  } catch (err) {
    if (wroteSecret) fs.unlinkSync(c.qq.app_secret_file);
    throw err;
  }
  pending = null;
  finishOperation();
  state('starting', { app_id: raw.qq.app_id, owner_openid: raw.qq.owner_openid }, id);
  binding(config(), id, 'confirm');
}
async function startBridge(id, c) {
  if (running) throw new Error('busy');
  const release = availableLock(c);
  const run = { id, c, release, controller: new AbortController(), bot: null, bridge: null, stopped: false, promise: null };
  running = run;
  state('starting', { app_id: c.qq.app_id, owner_openid: c.qq.owner_openid }, id);
  run.promise = (async () => {
    let log, stopTimer, gateway;
    try {
      const client = new AliceClient(c.alice, token(c));
      const first = await client.status();
      if (run.stopped) return;
      if (first.code !== 200 || first.data.protocol_version !== 1 || !['ready', 'busy'].includes(first.data.status)) throw new Error('alice_not_ready');
      const ledger = new Ledger(c.storage.state_dir);
      log = new SafeLog(c.storage.log_dir, { quiet: true });
      const disconnected = () => {
        if (running === run && !run.stopped && !closing) state('starting', { app_id: c.qq.app_id, owner_openid: c.qq.owner_openid, error_code: 'qq_reconnecting' }, run.id);
      };
      const bot = makeBot(c, null, disconnected, log);
      run.bot = bot;
      const bridge = new Bridge(c, ledger, log, client, bot);
      run.bridge = bridge; bridge.health = first.data;
      bot.on('message', (_ctx, msg) => {
        if (run.stopped || running !== run) return;
        try { bridge.receive(msg); }
        catch { bridge.stopRequested = true; bot.stop(); error('bridge_storage_failed', run.id); }
      });
      const online = () => {
        if (running === run && !run.stopped && !closing) state('connected', { app_id: c.qq.app_id, owner_openid: c.qq.owner_openid }, run.id);
      };
      bot.on('ready', online); bot.on('resumed', online); bot.on('error', disconnected);
      const stopFile = path.join(c.storage.state_dir, 'stop.request');
      if (fs.existsSync(stopFile)) fs.unlinkSync(stopFile);
      stopTimer = setInterval(() => {
        if (run.stopped || fs.existsSync(stopFile)) { run.stopped = true; bridge.stopRequested = true; bot.stop(); }
      }, 500);
      gateway = bot.start(run.controller.signal).catch(() => {
        bridge.stopRequested = true;
        if (!run.stopped) error('qq_start_failed', run.id);
      });
      await bridge.run();
    } catch (err) {
      if (!run.stopped && !closing) error(err.message === 'alice_not_ready' ? 'alice_not_ready' : 'bridge_failed', run.id);
    } finally {
      run.stopped = true; run.controller.abort(); run.bot?.stop();
      if (gateway) await Promise.race([gateway, sleep(15000)]);
      clearInterval(stopTimer);
      run.bot?.stop();
      try { log?.write('bridge_stopped'); } catch { /* Ledger, not logs, controls recovery. */ }
      try { release(); } catch { if (!closing) error('lock_release_failed', run.id); }
      if (running === run) running = null;
      if (!closing && current.state !== 'error') state('stopped', { app_id: c.qq.app_id, owner_openid: c.qq.owner_openid }, run.id);
    }
  })();
}
function stop(id = current.operation_id) {
  pending = null; awaitingBinding = null; finishOperation();
  if (!running) { state('stopped', {}, id); return; }
  running.id = id;
  running.stopped = true; running.controller.abort();
  if (running.bridge) running.bridge.stopRequested = true;
  running.bot?.stop();
  state('stopping', {}, running.id);
}
function status() {
  if (operation || running || awaitingBinding || current.state === 'error') { emit({ type: 'state', ...current }); return; }
  if (fs.existsSync(file)) {
    try {
      const c = config(true, true);
      if (fs.existsSync(path.join(c.storage.state_dir, 'bridge.lock'))) { error('external_bridge_or_stale_lock'); return; }
      if (validIdentity(c.qq.app_id) && validIdentity(c.qq.owner_openid) && fs.existsSync(c.qq.app_secret_file)) {
        state('configured', { app_id: c.qq.app_id, owner_openid: c.qq.owner_openid }); return;
      }
      if (validIdentity(c.qq.app_id) && fs.existsSync(c.qq.app_secret_file)) {
        state('configured', { app_id: c.qq.app_id, error_code: 'owner_pair_required' }); return;
      }
    } catch { error('config_invalid'); return; }
  }
  state('idle');
}
async function command(value) {
  if (closing) return;
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
    Object.keys(value).some(k => !['action', 'operation_id'].includes(k)) ||
    !['status', 'scan', 'pair', 'confirm', 'cancel', 'run', 'stop', 'configured', 'binding_failed'].includes(value.action)) throw new Error('command_invalid');
  const { action, operation_id: id } = value;
  if (!supportedNode) throw new Error('unsupported_node');
  if (action === 'status') { status(); return; }
  if (!validID(id)) throw new Error('command_invalid');
  if (action === 'stop') { stop(id); return; }
  if (action === 'cancel') {
    if (id !== current.operation_id) throw new Error('operation_stale');
    if (running) { stop(id); return; }
    finishOperation(); pending = null; awaitingBinding = null; state('idle', {}, id); return;
  }
  if (action === 'configured' || action === 'binding_failed') {
    if (!awaitingBinding || awaitingBinding.id !== id) return; // A stop/cancel may have invalidated this delayed acknowledgement.
    const ready = awaitingBinding; awaitingBinding = null;
    if (action === 'binding_failed') { error('binding_failed', id); return; }
    if (ready.action === 'run') await startBridge(id, ready.c);
    else state('configured', { app_id: ready.c.qq.app_id, owner_openid: ready.c.qq.owner_openid }, id);
    return;
  }
  if (running || awaitingBinding) throw new Error('busy');
  if (action === 'confirm') { confirm(id); return; }
  if (action === 'scan') { await scan(id); return; }
  if (action === 'pair') { await pair(id); return; }
  if (action === 'run') {
    if (operation || pending) throw new Error('busy');
    const c = runConfig();
    // Refuse an external running instance before asking the core to enable us.
    const release = availableLock(c); release();
    state('starting', { app_id: c.qq.app_id, owner_openid: c.qq.owner_openid }, id);
    binding(c, id, 'run');
  }
}
function close() {
  if (closing) return;
  closing = true;
  stop();
  // The parent owns this helper only. Stop accepting commands; wait for a
  // persisted in-flight send. Parent may terminate this process on shutdown.
  process.stdin.pause();
  if (!running) process.exit(0);
  else running.promise?.finally(() => process.exit(0));
}
process.on('SIGINT', close); process.on('SIGTERM', close);
process.stdin.on('end', close); process.stdin.on('error', close);
process.stdout.on('error', close);
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  if (closing) return;
  inputBuffer += chunk;
  if (inputBuffer.length > 8192) { close(); return; }
  let newline;
  while ((newline = inputBuffer.indexOf('\n')) >= 0) {
    const line = inputBuffer.slice(0, newline); inputBuffer = inputBuffer.slice(newline + 1);
    if (!line.trim()) continue;
    commandChain = commandChain.then(async () => {
      let value;
      try { value = JSON.parse(line); await command(value); }
      catch (err) {
        const safe = new Set(['busy', 'already_configured', 'binding_conflict', 'not_configured', 'owner_pair_required', 'unsupported_node', 'command_invalid', 'confirmation_stale', 'operation_stale', 'external_bridge_or_stale_lock', 'token_invalid']);
        error(safe.has(err.message) ? err.message : 'operation_failed', validID(value?.operation_id) ? value.operation_id : current.operation_id);
      }
    });
  }
});
emit({ type: 'state', ...current });
