import { hash } from './storage.mjs';

const DONE = new Set(['platform_confirmed', 'send_unknown', 'failed', 'expired', 'cancelled', 'quarantined', 'interrupted', 'rejected']);
const GENERATING = new Set(['accepted', 'running', 'user_committed']);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const codeOf = data => data.error_code ?? data.code ?? (typeof data.error === 'object' ? data.error?.code : data.error);

export function textSegments(text, maxSegments) {
  const suffix = '\n（已截断，完整回复请在本机 GUI 查看。）';
  const input = text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '');
  const items = [...new Intl.Segmenter('zh', { granularity: 'grapheme' }).segment(input)].map(x => x.segment);
  const parts = []; let part = '', count = 0;
  for (const item of items) {
    const len = [...item].length;
    if (len > 1000 || Buffer.byteLength(item) > 4000) continue;
    if (count + len > 1000 || Buffer.byteLength(part + item) > 4000) {
      parts.push(part); part = ''; count = 0;
      if (parts.length === maxSegments) {
        const last = [...new Intl.Segmenter('zh', { granularity: 'grapheme' }).segment(parts.at(-1))].map(x => x.segment);
        while (last.length && ([...(last.join('') + suffix)].length > 1000 || Buffer.byteLength(last.join('') + suffix) > 4000)) last.pop();
        parts[parts.length - 1] = last.join('') + suffix;
        return parts;
      }
    }
    part += item; count += len;
  }
  if (part) parts.push(part);
  return parts.length ? parts : ['（沉默）'];
}

export class Bridge {
  constructor(config, ledger, log, client, bot) {
    this.config = config; this.ledger = ledger; this.log = log; this.client = client; this.bot = bot;
    this.health = null; this.stopRequested = false; this.faulted = false; this.lastSend = 0;
  }
  save() {
    try { this.ledger.save(); }
    catch { this.faulted = true; this.stopRequested = true; this.bot.stop(); throw new Error('ledger_write_failed'); }
  }
  validBinding(r) {
    return r.source.app_id === this.config.qq.app_id && r.source.sender_id === this.config.qq.owner_openid &&
      r.source.provider === 'qq-official' && r.source.chat_type === 'c2c';
  }
  remaining(r) { return r.deadline - Date.now(); }
  notice(r, text, status = 'outbox') {
    r.kind = 'notice'; r.status = status; delete r.text;
    r.segments = textSegments(text, 1).map((text, index) => ({ index, seq: index + 1, text, hash: hash(text), status: 'ready', attempts: 0 }));
  }
  receive(msg) {
    if (this.stopRequested || this.faulted || msg.rawEventType !== 'C2C_MESSAGE_CREATE' || msg.kind !== 'c2c' ||
        msg.senderId !== this.config.qq.owner_openid || msg.senderIsBot) return;
    const raw = msg.raw;
    if (!raw || raw.author?.user_openid !== msg.senderId || raw.id !== msg.messageId ||
        typeof raw.id !== 'string' || raw.id.length > 256 || !raw.id) return;
    const timestamp = Date.parse(raw.timestamp);
    if (!Number.isFinite(timestamp) || timestamp > Date.now() + 30000 || Date.now() - timestamp > 300000) {
      this.log.write('stale_event'); return;
    }
    const key = hash(JSON.stringify(['qq-official', this.config.qq.app_id, 'c2c', msg.senderId, raw.id]));
    const content = typeof raw.content === 'string' ? raw.content : '';
    const payloadHash = hash(content);
    const old = this.ledger.data.records.find(r => r.event_key === key);
    if (old) { if (old.payload_hash !== payloadHash) this.log.write('event_conflict', key); return; }
    const r = {
      event_key: key, payload_hash: payloadHash, request_id: hash(`${this.ledger.data.bridge_id}:${key}`),
      session_epoch: this.health?.session_epoch ?? null, received_at: Date.now(),
      deadline: timestamp + this.config.delivery.passive_window_seconds * 1000,
      queue_until: Math.min(Date.now() + this.config.delivery.queue_ttl_seconds * 1000, timestamp + this.config.delivery.passive_window_seconds * 1000 - 30000),
      source: { provider: 'qq-official', app_id: this.config.qq.app_id, chat_type: 'c2c', sender_id: msg.senderId,
        conversation_id: `c2c:${msg.senderId}`, message_id: raw.id, platform_time: new Date(timestamp).toISOString(),
        ...(typeof msg.msgIdx === 'string' && msg.msgIdx.length <= 256 ? { msg_idx: msg.msgIdx } : {}) },
      text: content.trim(), kind: 'chat', status: 'queued', retry_at: 0, retries: 0,
    };
    const media = msg.attachments?.length || msg.refMsgIdx || msg.msgElements?.length || raw.message_reference ||
      raw.ark || raw.embed || (msg.msgType != null && ![0, 100].includes(msg.msgType)) || /<face\b/i.test(content);
    if (media) this.notice(r, '目前只支持纯文字，请去掉图片、语音、文件、引用或平台表情后发送。');
    else if (!r.text || [...r.text].length > 4000 || Buffer.byteLength(r.text) > 12000 ||
        Buffer.byteLength(JSON.stringify({ source: r.source, text: r.text })) > 14000) {
      this.notice(r, '消息为空或过长，请缩短为纯文字后发送。');
    } else if (r.text.startsWith('//')) r.text = r.text.slice(1);
    else if (r.text === '/status') {
      const last = [...this.ledger.data.records].reverse().find(x => x.kind === 'chat' && this.validBinding(x));
      const labels = { queued: '等待提交', submitting: '正在确认是否已接收', accepted: '正在生成', outbox: '回复已生成，正在发送',
        platform_confirmed: '平台已确认发送（不代表已读）', send_unknown: '发送结果未知，请先查看 QQ 或本机网页', failed: '处理或发送失败，请查看本机网页',
        expired: '发送窗口已过期，请在本机网页查看', interrupted: '处理已中断，请在本机网页确认', quarantined: '会话或授权已变化，旧记录已隔离', cancelled: '已取消等待' };
      this.notice(r, last ? `最新请求：${labels[last.status] ?? '处理中'}。` : '还没有收到可处理的聊天。');
    } else if (r.text === '/cancel') {
      const pending = [...this.ledger.data.records].reverse().find(x => x.kind === 'chat' && x.status === 'queued' && this.validBinding(x));
      if (pending) { pending.status = 'cancelled'; delete pending.text; }
      this.notice(r, pending ? '已取消最近一条尚未提交的消息。' : '没有可取消的等待消息；已经提交的当前轮会继续完成。');
    } else if (r.text.startsWith('/')) {
      this.notice(r, '这里只支持 /status 和 /cancel。管理操作请在本机网页进行；字面斜杠消息可用 // 开头。');
    }
    if (r.kind === 'chat') {
      if (!this.health || !['ready', 'busy'].includes(this.health.status)) this.notice(r, 'Alice 暂未就绪，这条消息未提交，请稍后重发。');
      else if (this.ledger.data.records.filter(x => x.kind === 'chat' && x.status === 'queued').length >= this.config.delivery.queue_limit) {
        this.notice(r, '等待消息已满，这条消息未提交，请稍后重发。');
      }
    }
    try { this.ledger.add(r); }
    catch { this.faulted = true; this.stopRequested = true; this.bot.stop(); throw new Error('ledger_write_failed'); }
    this.log.write('event_recorded', r.request_id);
  }
  async refresh() {
    const { code, data } = await this.client.status();
    if (code === 401 || code === 403) { this.stopRequested = true; this.log.write('alice_authorization_failed'); return false; }
    if (code !== 200 || data.protocol_version !== 1 || typeof data.session_epoch !== 'string') { this.health = null; return false; }
    this.health = data;
    if (!['ready', 'busy'].includes(data.status)) return false;
    return true;
  }
  expire(r, status = 'expired') {
    r.status = status; delete r.text;
    for (const seg of r.segments ?? []) {
      delete seg.text;
      if (status === 'expired' && ['ready', 'retry_wait'].includes(seg.status)) {
        seg.status = 'failed'; seg.error_code = 'passive_window_expired';
      }
    }
    this.save(); this.log.write(status, r.request_id);
  }
  async stepRequest(r) {
    if (r.status === 'queued') {
      if (Date.now() > r.queue_until) {
        this.notice(r, '等待已超时，这条消息尚未交给 Alice，请稍后重发。'); this.save(); return;
      }
      if (Date.now() < r.retry_at || this.health.status !== 'ready') return;
      r.status = 'submitting'; this.save();
      // Persist first: if the POST response disappears, recovery queries this exact ID.
      const response = await this.client.submit({ channel_id: this.config.alice.channel_id, session_epoch: r.session_epoch,
        request_id: r.request_id, source: r.source, text: r.text });
      if (response.code === 200 || response.code === 202) return this.consume(r, response.data);
      const code = codeOf(response.data);
      if (response.code === 409 && code === 'busy') {
        r.status = 'queued'; r.retries++; r.retry_at = Date.now() + Math.min(8000, 1000 * 2 ** Math.min(r.retries, 3)) + Math.random() * 500; this.save(); return;
      }
      if (response.code === 503) return; // Unknown or draining: query before any repeat POST.
      this.expire(r, 'failed'); return;
    }
    const { code, data } = await this.client.result(r.request_id);
    if (code === 200) return this.consume(r, data);
    if (code === 404 && r.status === 'submitting' && typeof r.text === 'string') {
      r.status = 'queued'; this.save(); return;
    }
    if (code === 401 || code === 403) { this.stopRequested = true; this.log.write('alice_authorization_failed'); }
    else if (code === 409) this.expire(r, 'quarantined');
    else if (code === 404) this.expire(r, 'interrupted');
  }
  consume(r, data) {
    if (data.session_epoch !== r.session_epoch || typeof data.request_id !== 'string') throw new Error('invalid_result_identity');
    r.request_id = data.request_id; // Stable platform source may already belong to a previous installation's request.
    if (typeof data.reply === 'string' && ['generated', 'completed', 'completed_with_warning'].includes(data.status)) {
      r.reply_hash = hash(data.reply); delete r.text;
      r.segments = textSegments(data.reply || '（沉默）', this.config.delivery.max_reply_segments)
        .map((text, index) => ({ index, seq: index + 1, text, hash: hash(text), status: 'ready', attempts: 0 }));
      r.status = 'outbox'; this.save();
    } else if (GENERATING.has(data.status)) { r.status = 'accepted'; delete r.text; this.save(); }
    else if (['failed', 'interrupted', 'invalidated'].includes(data.status)) {
      r.error_code = data.error_code;
      this.expire(r, data.status === 'failed' ? 'failed' : 'interrupted');
    }
  }
  async report(r, seg, status = seg.status) {
    if (r.kind !== 'chat' || !r.reply_hash || seg.reported_status === status) return true;
    // A local sending checkpoint may precede the core's sending receipt. Replay
    // these audit transitions, never the QQ send itself. Conflicts can mean a
    // prior terminal receipt already committed, so still try that same terminal.
    if (['platform_confirmed', 'failed', 'send_unknown'].includes(status)) {
      if (!seg.registered) await this.report(r, seg, 'ready');
      if (!['sending', status].includes(seg.reported_status)) await this.report(r, seg, 'sending');
    }
    const { code } = await this.client.delivery({ request_id: r.request_id, session_epoch: r.session_epoch,
      segment_index: seg.index, segment_hash: seg.hash, reply_hash: r.reply_hash, status,
      ...(seg.platform_message_id ? { platform_message_id: seg.platform_message_id } : {}),
      ...(seg.error_code ? { error_code: seg.error_code } : {}) });
    if (code === 200 || code === 201) { seg.reported_status = status; if (status === 'ready') seg.registered = true; this.save(); return true; }
    return false;
  }
  async sendSegment(r, seg) {
    if (!seg.registered && !await this.report(r, seg, 'ready')) return;
    if (seg.retry_at && Date.now() < seg.retry_at) return;
    if (this.remaining(r) < 15000) { this.expire(r); return; }
    if (Date.now() - this.lastSend < this.config.delivery.send_spacing_ms) return;
    // Recheck immediately before dispatch. A remote in-flight send still cannot be recalled.
    if (!await this.refresh() || this.health.session_epoch !== r.session_epoch || this.stopRequested) return;
    seg.status = 'sending'; seg.attempts++; this.save();
    try {
      if (!await this.report(r, seg)) { seg.status = 'retry_wait'; this.save(); return; }
    } catch {
      // We have not called QQ yet. Retry only the core audit transition.
      seg.status = 'retry_wait'; this.save(); return;
    }
    this.lastSend = Date.now();
    try {
      const result = await this.bot.api.post(`/v2/users/${encodeURIComponent(r.source.sender_id)}/messages`, {
        msg_type: 0, content: seg.text, msg_id: r.source.message_id, msg_seq: seg.seq,
      });
      if (result?.code || result?.err_code) {
        seg.status = 'failed'; seg.error_code = 'qq_business_rejection';
      } else if (typeof result?.id !== 'string' || !result.id) {
        seg.status = 'send_unknown'; seg.error_code = 'qq_missing_receipt';
      } else { seg.status = 'platform_confirmed'; seg.platform_message_id = result.id; delete seg.text; }
    } catch (error) {
      const status = error?.httpStatus;
      if (status === 401 && seg.attempts < 2) {
        this.bot.tokenManager.clearCache(this.config.qq.app_id);
        seg.status = 'retry_wait'; seg.retry_at = Date.now() + 1000;
      } else if (status === 429 && seg.attempts < 3 && this.remaining(r) > 30000) {
        seg.status = 'retry_wait'; seg.retry_at = Date.now() + 5000 * seg.attempts + Math.random() * 1000;
      } else if ([400, 401, 403, 404, 413, 422, 429].includes(status)) {
        seg.status = 'failed'; seg.error_code = `qq_http_${status}`;
        if (status === 401 || status === 403) this.stopRequested = true;
      } else { seg.status = 'send_unknown'; seg.error_code = 'qq_result_unknown'; }
    }
    if (['failed', 'send_unknown'].includes(seg.status)) r.status = seg.status;
    else if (r.segments.every(s => s.status === 'platform_confirmed')) r.status = 'platform_confirmed';
    this.save();
    this.log.write(seg.status, r.request_id);
    if (seg.status !== 'retry_wait') await this.report(r, seg);
  }
  async tick() {
    if (!await this.refresh()) return;
    for (const r of this.ledger.data.records) {
      if ((!this.validBinding(r) || r.session_epoch !== this.health.session_epoch) && !DONE.has(r.status)) this.expire(r, 'quarantined');
    }
    // Receipt updates may recover after local Alice shutdown, but never trigger another QQ send.
    for (const r of this.ledger.data.records) {
      if (!this.validBinding(r) || r.session_epoch !== this.health.session_epoch) continue;
      for (const seg of r.segments ?? []) {
        if (['platform_confirmed', 'failed', 'send_unknown'].includes(seg.status) && seg.reported_status !== seg.status) {
          if (!await this.report(r, seg)) return;
        }
      }
    }
    const outgoing = this.ledger.data.records.find(r => r.status === 'outbox');
    if (outgoing) {
      if (this.remaining(outgoing) <= 15000) this.expire(outgoing);
      else {
        for (const item of outgoing.segments) {
          if (!item.registered && !await this.report(outgoing, item, 'ready')) return;
        }
        const seg = outgoing.segments.find(s => ['ready', 'retry_wait'].includes(s.status));
        if (seg) await this.sendSegment(outgoing, seg);
      }
      return; // Finish/audit the previous reply before occupying Alice with another turn.
    }
    const request = this.ledger.data.records.find(r => r.kind === 'chat' && ['submitting', 'accepted'].includes(r.status)) ??
      this.ledger.data.records.find(r => r.kind === 'chat' && r.status === 'queued');
    if (request) await this.stepRequest(request);
  }
  async run() {
    let nextPrune = 0;
    while (!this.stopRequested) {
      try {
        if (Date.now() > nextPrune) { this.ledger.prune(); this.save(); nextPrune = Date.now() + 60000; }
        await this.tick();
      } catch {
        this.health = null;
        this.log.write(this.faulted ? 'ledger_write_failed' : 'bridge_waiting_for_service');
      }
      await sleep(1000);
    }
    this.bot.stop();
  }
}
