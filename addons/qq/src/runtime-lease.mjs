// Project-wide use/installation handshake; never contains account information.
// Keep the lease until process exit because imported modules remain cached.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const addonRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function acquireRuntimeLease() {
  const nonce = randomUUID();
  const directory = path.join(addonRoot, '.runtime-leases');
  const file = path.join(directory, `${process.pid}-${nonce}.json`);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  let created = false;
  try {
    const fd = fs.openSync(file, 'wx', 0o600);
    created = true;
    try {
      fs.writeFileSync(fd, JSON.stringify({ pid: process.pid, nonce }));
      fs.fsyncSync(fd);
    } finally { fs.closeSync(fd); }
  } catch (error) {
    if (created) {
      try { fs.unlinkSync(file); } catch { /* Keep an unremovable file for review. */ }
    }
    throw error;
  }

  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    process.off('exit', release);
    try {
      const value = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (value.pid === process.pid && value.nonce === nonce) fs.unlinkSync(file);
    } catch { /* A surviving lease is conservatively checked by the installer. */ }
  };
  process.once('exit', release);
  try {
    // Publish our lease before checking the installer. The installer does the
    // reverse: acquire its lock before examining leases. At least one must stop.
    // Even an empty/corrupt/stale installation lock blocks this process; only
    // the installer's PID-aware recovery may reclaim that lock.
    try { fs.lstatSync(path.join(addonRoot, '.install-lock')); }
    catch (error) {
      if (error.code === 'ENOENT') return release;
      throw error;
    }
    throw new Error('installation_busy');
  } catch (error) {
    release();
    throw error;
  }
}
