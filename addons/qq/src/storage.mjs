import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { adoptLegacyPlan, validatePlan, stopUnsent } from './delivery-plan.mjs';
export { freezePlan, stopUnsent } from './delivery-plan.mjs';

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
const RECORD_STATES = new Set(['queued', 'submitting', 'accepted', 'outbox', 'platform_confirmed', 'send_unknown', 'failed', 'expired', 'cancelled', 'quarantined', 'interrupted', 'rejected']);
const TERMINAL = new Set(['platform_confirmed', 'send_unknown', 'failed', 'expired', 'cancelled', 'quarantined', 'interrupted', 'rejected']);
const SEGMENT_STATES = new Set(['ready', 'sending', 'retry_wait', 'platform_confirmed', 'failed', 'send_unknown']);
const RECEIPT_STATES = new Set(['ready', 'sending', 'platform_confirmed', 'failed', 'send_unknown']);
const HASH = /^[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
// Existing jittered retry timestamps can contain fractional milliseconds.
const timestamp = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 8640000000000000;
const count = value => Number.isSafeInteger(value) && value >= 0;
const safeString = (value, maximum = 256) => typeof value === 'string' && value.length > 0 && value.length <= maximum
  && !/[\u0000-\u001f\u007f]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const corrupt = () => { throw new Error('桥账本损坏或版本不支持；原文件保留，不自动清空'); };

function validateRecord(record, version, now) {
  const r = record;
  if (!r || typeof r !== 'object' || !HASH.test(r.event_key ?? '') || !HASH.test(r.payload_hash ?? '') ||
      !/^[A-Za-z0-9_-]{16,128}$/.test(r.request_id ?? '') || !RECORD_STATES.has(r.status) ||
      !['chat', 'notice'].includes(r.kind) || !timestamp(r.received_at) || !timestamp(r.deadline) ||
      r.received_at > now + 1000 || !timestamp(r.queue_until) || !timestamp(r.retry_at) || !count(r.retries) ||
      !(r.session_epoch === null || /^[a-f0-9]{32}$/.test(r.session_epoch ?? '')) ||
      (r.kind === 'chat' && r.session_epoch === null) || !r.source || typeof r.source !== 'object') corrupt();
  const source = r.source;
  const sourceTime = Date.parse(source.platform_time);
  if (source.provider !== 'qq-official' || source.chat_type !== 'c2c' ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(source.app_id ?? '') || !/^[A-Za-z0-9_-]{1,128}$/.test(source.sender_id ?? '') ||
      source.conversation_id !== `c2c:${source.sender_id}` || !safeString(source.message_id) ||
      !Number.isFinite(sourceTime) || r.deadline < sourceTime + 60000 || r.deadline > sourceTime + 300000 ||
      r.queue_until > r.deadline || (r.reply_hash !== undefined && !HASH.test(r.reply_hash)) ||
      r.event_key !== hash(JSON.stringify(['qq-official', source.app_id, 'c2c', source.sender_id, source.message_id])) ||
      (r.text !== undefined && typeof r.text !== 'string')) corrupt();
  if (r.segments !== undefined) {
    if (!Array.isArray(r.segments) || r.segments.length < 1 || r.segments.length > 3 ||
        ['queued', 'submitting', 'accepted'].includes(r.status) ||
        (r.kind === 'chat' && !HASH.test(r.reply_hash ?? ''))) corrupt();
    for (const [index, s] of r.segments.entries()) {
      if (!s || typeof s !== 'object' || s.index !== index || s.seq !== index + 1 || !HASH.test(s.hash ?? '') ||
          !SEGMENT_STATES.has(s.status) || !count(s.attempts) ||
          (s.registered !== undefined && typeof s.registered !== 'boolean') ||
          (s.reported_status !== undefined && !RECEIPT_STATES.has(s.reported_status)) ||
          (s.retry_at !== undefined && !timestamp(s.retry_at)) ||
          (s.platform_message_id !== undefined && !safeString(s.platform_message_id)) ||
          (s.error_code !== undefined && !safeString(s.error_code)) ||
          (s.status === 'platform_confirmed' && !safeString(s.platform_message_id)) ||
          (s.text !== undefined && (typeof s.text !== 'string' || !s.text.length || [...s.text].length > 1000 ||
            Buffer.byteLength(s.text, 'utf8') > 4000 || hash(s.text) !== s.hash))) corrupt();
    }
    if (r.status === 'platform_confirmed' && r.segments.some(s => s.status !== 'platform_confirmed')) corrupt();
    if (version === 2) validatePlan(r);
    else if (r.plan !== undefined) corrupt();
  } else if (r.plan !== undefined || r.status === 'outbox' || r.status === 'platform_confirmed' || r.status === 'send_unknown') corrupt();
}

function validateLedger(data, now) {
  if (!data || ![1, 2].includes(data.version) || !UUID.test(data.bridge_id ?? '') ||
      !Array.isArray(data.records) || data.records.length > 10000) corrupt();
  if (data.version === 2 && (!timestamp(data.last_dispatch_at) || !timestamp(data.not_before) || data.last_dispatch_at > now + 1000)) {
    throw new Error('桥账本发送时钟无效或系统时钟回拨；保留原件并停止');
  }
  const events = new Set(), requests = new Set();
  for (const record of data.records) {
    validateRecord(record, data.version, now);
    if (events.has(record.event_key) || requests.has(record.request_id)) corrupt();
    events.add(record.event_key);
    requests.add(record.request_id);
  }
}

function recoverRecord(record) {
  let uncertain = false;
  for (const segment of record.segments ?? []) {
    if (segment.status !== 'sending') continue;
    segment.status = 'send_unknown';
    segment.error_code = 'process_interrupted';
    uncertain = true;
  }
  if (uncertain || record.segments?.some(s => s.status === 'send_unknown')) {
    // Preserve an explicit binding quarantine; it must never be revived as an outbox.
    if (record.status !== 'quarantined') record.status = 'send_unknown';
    stopUnsent(record, 'blocked_by_previous_unknown');
  } else if (record.segments?.some(s => s.status === 'failed')) {
    if (!TERMINAL.has(record.status)) record.status = 'failed';
    stopUnsent(record, 'blocked_by_previous_failure');
  } else if (TERMINAL.has(record.status)) {
    stopUnsent(record, record.status === 'expired' ? 'passive_window_expired' : 'plan_already_stopped');
  } else if (record.segments?.some(s => ['ready', 'retry_wait'].includes(s.status) && typeof s.text !== 'string')) {
    record.status = 'failed'; record.error_code = 'missing_segment_text';
    stopUnsent(record, 'missing_segment_text');
  } else if (record.status === 'queued' && typeof record.text !== 'string') {
    record.status = 'failed'; record.error_code = 'missing_request_text';
  } else if (record.segments?.every(s => s.status === 'platform_confirmed')) {
    record.status = 'platform_confirmed';
  }
}

export class Ledger {
  constructor(dir) {
    this.file = path.join(dir, 'state.json');
    this.identityFile = path.join(dir, 'ledger.identity.json');
    const now = Date.now();
    let identity;
    if (fs.existsSync(this.identityFile)) {
      if (fs.statSync(this.identityFile).size > 1024) corrupt();
      identity = JSON.parse(fs.readFileSync(this.identityFile, 'utf8'));
      if (identity?.version !== 1 || !UUID.test(identity.bridge_id ?? '')) corrupt();
    }
    if (fs.existsSync(this.file)) {
      if (fs.statSync(this.file).size > 24 * 1024 * 1024) throw new Error('桥账本过大，已停止；请保留文件人工排查');
      this.data = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      validateLedger(this.data, now); // No disk mutation until every record has passed validation.
      if (identity && identity.bridge_id !== this.data.bridge_id) corrupt();
      if (this.data.version === 1) {
        for (const record of this.data.records) adoptLegacyPlan(record);
        this.data.version = 2;
        this.data.last_dispatch_at = now; // No old send clock: wait at least the configured interval.
        this.data.not_before = Math.max(now + 1000, ...this.data.records.flatMap(r => (r.segments ?? []).map(s => s.retry_at ?? 0)));
      }
    } else {
      if (identity) throw new Error('桥账本已丢失；保留身份文件并停止，不自动初始化或重发');
      this.data = { version: 2, bridge_id: randomUUID(), records: [], last_dispatch_at: 0, not_before: 0 };
    }
    validateLedger(this.data, now);
    for (const record of this.data.records) recoverRecord(record);
    this.prune(); this.save();
    if (!identity) atomicJSON(this.identityFile, { version: 1, bridge_id: this.data.bridge_id });
  }
  save() { atomicJSON(this.file, this.data); }
  prune() {
    const now = Date.now();
    for (const r of this.data.records) {
      if (now - r.received_at > 86400000) {
        recoverRecord(r);
        stopUnsent(r, 'retention_expired');
        if (!TERMINAL.has(r.status)) r.status = 'expired';
        delete r.text;
        for (const s of r.segments ?? []) delete s.text;
      }
    }
    this.data.records = this.data.records.filter(r => !TERMINAL.has(r.status) || now - r.received_at <= 30 * 86400000);
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
