import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';

export const hash = value => createHash('sha256').update(value).digest('hex');
export function privateDirectory(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (process.platform === 'win32') {
    const sid = execFileSync('whoami.exe', ['/user', '/fo', 'csv', '/nh'], { encoding: 'utf8', windowsHide: true }).match(/S-1-[0-9-]+/)?.[0];
    if (!sid) throw new Error('无法确认当前Windows用户；未保存秘密');
    execFileSync('icacls.exe', [dir, '/inheritance:r', '/grant:r', `*${sid}:(OI)(CI)F`, '*S-1-5-18:(OI)(CI)F'], { windowsHide: true, stdio: 'ignore' });
  }
}
export function atomicJSON(file, value) {
  const tmp = `${file}.${randomUUID()}.tmp`;
  let fd;
  try {
    fd = fs.openSync(tmp, 'wx', 0o600);
    fs.writeFileSync(fd, JSON.stringify(value));
    fs.fsyncSync(fd);
    fs.closeSync(fd); fd = undefined;
    fs.renameSync(tmp, file);
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
    if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
  }
}
export function acquireLock(dir) {
  privateDirectory(dir);
  const file = path.join(dir, 'bridge.lock');
  const nonce = randomUUID();
  try {
    const fd = fs.openSync(file, 'wx', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify({ pid: process.pid, nonce })); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
  } catch (e) {
    if (e.code === 'EEXIST') throw new Error('桥已有实例或上次异常退出留下锁；确认进程已退出后运行 unlock');
    throw e;
  }
  return () => {
    if (JSON.parse(fs.readFileSync(file, 'utf8')).nonce === nonce) fs.unlinkSync(file);
  };
}
export function unlockStale(dir) {
  const file = path.join(dir, 'bridge.lock');
  const info = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!Number.isSafeInteger(info.pid) || info.pid <= 0) throw new Error('锁损坏，请人工核对；未移除');
  try { process.kill(info.pid, 0); }
  catch (e) { if (e.code === 'ESRCH') { fs.unlinkSync(file); return; } throw e; }
  throw new Error('该 PID 仍存在；为避免误操作，未移除锁');
}
export class Ledger {
  constructor(dir) {
    this.file = path.join(dir, 'state.json');
    if (fs.existsSync(this.file)) {
      if (fs.statSync(this.file).size > 24 * 1024 * 1024) throw new Error('桥账本过大，已停止；请保留文件人工排查');
      this.data = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      const d = this.data;
      const statuses = new Set(['queued', 'submitting', 'accepted', 'outbox', 'platform_confirmed', 'send_unknown', 'failed', 'expired', 'cancelled', 'quarantined', 'interrupted', 'rejected']);
      if (d.version !== 1 || typeof d.bridge_id !== 'string' || !Array.isArray(d.records) || d.records.length > 10000 ||
          d.records.some(r => !r || !/^[a-f0-9]{64}$/.test(r.event_key) || !/^[A-Za-z0-9_-]{16,128}$/.test(r.request_id) ||
            !Number.isFinite(r.received_at) || !Number.isFinite(r.deadline) || !r.source || !statuses.has(r.status) ||
            !['chat', 'notice'].includes(r.kind) || (r.segments && (!Array.isArray(r.segments) || r.segments.length > 3 ||
              r.segments.some((s, i) => !s || s.index !== i || s.seq !== i + 1 || !/^[a-f0-9]{64}$/.test(s.hash) ||
                !['ready', 'sending', 'retry_wait', 'platform_confirmed', 'failed', 'send_unknown'].includes(s.status)))))) {
        throw new Error('桥账本损坏或版本不支持；原文件保留，不自动清空');
      }
    } else {
      this.data = { version: 1, bridge_id: randomUUID(), records: [] };
      this.save();
    }
    for (const r of this.data.records) {
      if (r.segments) for (const seg of r.segments) {
        if (seg.status === 'sending') { seg.status = 'send_unknown'; r.status = 'send_unknown'; }
      }
    }
    this.prune(); this.save();
  }
  save() { atomicJSON(this.file, this.data); }
  prune() {
    const now = Date.now();
    const terminal = new Set(['platform_confirmed', 'send_unknown', 'failed', 'expired', 'cancelled', 'quarantined', 'interrupted', 'rejected']);
    for (const r of this.data.records) {
      if (now - r.received_at > 86400000) {
        delete r.text;
        for (const s of r.segments ?? []) delete s.text;
        if (!terminal.has(r.status)) r.status = 'expired';
      }
    }
    this.data.records = this.data.records.filter(r => !terminal.has(r.status) || now - r.received_at <= 30 * 86400000);
  }
  add(record) {
    this.prune();
    if (this.data.records.length >= 10000) throw new Error('桥账本达到上限；停止接单，保留进行中记录');
    this.data.records.push(record); this.save();
  }
}
export class SafeLog {
  constructor(dir, { quiet = false } = {}) { this.dir = dir; this.quiet = quiet; privateDirectory(dir); }
  write(code, id = '') {
    // Callers only pass fixed categories, never platform error text or payloads.
    if (!/^[a-z0-9_]{1,64}$/.test(code)) code = 'internal_error';
    const now = new Date();
    const file = path.join(this.dir, `${now.toISOString().slice(0, 10)}.log`);
    for (const name of fs.readdirSync(this.dir)) {
      if (/^\d{4}-\d{2}-\d{2}\.log$/.test(name) && now - new Date(name.slice(0, 10)) > 7 * 86400000) fs.unlinkSync(path.join(this.dir, name));
    }
    if (!fs.existsSync(file) || fs.statSync(file).size < 2 * 1024 * 1024) {
      fs.appendFileSync(file, JSON.stringify({ at: now.toISOString(), code, ref: id ? hash(id).slice(0, 12) : '' }) + '\n', { mode: 0o600 });
    }
    if (!this.quiet) console.log(`[${now.toISOString()}] ${code}${id ? ` #${hash(id).slice(0, 12)}` : ''}`);
  }
}
