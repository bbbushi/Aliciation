import { createHash, randomUUID } from 'node:crypto';
import { lstat, realpath } from 'node:fs/promises';
import { isAbsolute, resolve } from 'node:path';
import { createUserMessage, lastAssistantStreamChunk } from '@deepseek-ai/dsh-llm';
import { setApprovalPolicy } from '@deepseek-ai/dsh-user-approval';
import { MaintenanceError, maintainSession, managementCapabilities } from './maintenance.mjs';

export const PROTOCOL = 'alice-dsh-plugin-bridge-v1';
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const MODES = new Set(['read-only', 'workspace-write']);
const MAX_RECORDS = 64;
const MAX_HANDLES = 16;
const MAX_IDENTITIES = 4096;
const MAX_RECEIPTS = 8192;
const MAX_EVENTS = 512;
const MAX_EVENT_BYTES = 512 * 1024;
const PAGE_BYTES = 128 * 1024;
const TEXT_BYTES = 32 * 1024;
const IDLE_MS = 15 * 60 * 1000;
const STOP_WAIT_MS = 15000;
const CREATION_MS = 10000;
const BLOCKED_TOOLS = /^(?:plugin_manager|subagent(?:_.+)?|spawn_agent|spawn_subagent|spawn_teammate|list_subagent_models|send_message|interrupt_agent|list_agents|wait_agent|team_task_.+)$/;

export class BridgeError extends Error {
  constructor(code, status = 400) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

function requireValue(condition, code = 'invalid_request', status = 400) {
  if (!condition) throw new BridgeError(code, status);
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}

function exact(value, required, optional = []) {
  requireValue(object(value));
  const allowed = new Set([...required, ...optional]);
  requireValue(required.every((key) => Object.hasOwn(value, key))
    && Object.keys(value).every((key) => allowed.has(key)));
}

function integer(value, min, max) {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (!object(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

function digest(value) {
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}

function publicText(value, token) {
  if (typeof value !== 'string') return { text: '', text_truncated: false };
  let text = value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
    .replace(/[\u202a-\u202e\u2066-\u2069]/g, '');
  if (token) text = text.split(token).join('[redacted]');
  text = text.replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+/gi, 'Bearer [redacted]')
    .replace(/\b(?:sk|pk)-(?:proj-)?[A-Za-z0-9_-]{16,}\b/g, '[redacted]')
    .replace(/((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[=:]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&]+)/gi, '$1[redacted]');
  const bytes = Buffer.from(text, 'utf8');
  if (bytes.length <= TEXT_BYTES) return { text, text_truncated: false };
  // The final partial UTF-8 code point is discarded, never replaced by raw bytes.
  text = bytes.subarray(0, TEXT_BYTES).toString('utf8').replace(/\ufffd$/, '');
  return { text, text_truncated: true };
}

function toolName(value) {
  return typeof value === 'string' && /^[A-Za-z0-9_.:/-]{1,128}$/.test(value)
    ? value : 'unavailable';
}

function emptyUsage() {
  return {
    status: 'unknown', source: 'dsh_reported', model_requests: 0, reported_requests: 0,
    input_tokens: null, output_tokens: null, cache_read_tokens: null, cache_write_tokens: null,
    total_tokens: null, cost: null, cost_status: 'unknown', coverage: 'owned_agent_only',
  };
}

function usageSample(value) {
  if (!object(value) || !integer(value.inputTokens, 0, 1e12)
      || !integer(value.outputTokens, 0, 1e12)) return null;
  for (const key of ['cacheReadTokens', 'cacheWriteTokens']) {
    if (value[key] !== undefined && !integer(value[key], 0, 1e12)) return null;
  }
  return {
    input_tokens: value.inputTokens, output_tokens: value.outputTokens,
    cache_read_tokens: value.cacheReadTokens ?? 0, cache_write_tokens: value.cacheWriteTokens ?? 0,
  };
}

async function defaultAgentPreset(ctx) {
  let timer;
  try {
    const preset = await Promise.race([
      Promise.resolve().then(() => ctx.agentPresets.resolve()),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new BridgeError('agent_preset_unavailable', 409)), CREATION_MS);
        timer.unref();
      }),
    ]);
    requireValue(preset && typeof preset.id === 'string' && preset.id.length > 0
      && preset.broken === undefined, 'agent_preset_unavailable', 409);
    return preset.id;
  } catch {
    throw new BridgeError('agent_preset_unavailable', 409);
  } finally {
    clearTimeout(timer);
  }
}

export function capabilities(ctx) {
  return {
    agents_create: typeof ctx.agents?.create === 'function',
    sessions_get: typeof ctx.sessions?.get === 'function',
    session_persistence_stat: typeof ctx.sessionPersistence?.stat === 'function',
    permission_set: typeof ctx.permissionPresets?.set === 'function',
    permission_current: typeof ctx.permissionPresets?.current === 'function',
    sandbox_resolve: typeof ctx.sandboxPolicy?.resolve === 'function',
    approval_effective: typeof ctx.approval?.effectivePolicy === 'function',
    default_model: typeof ctx.agentDefaultModel?.currentSelection === 'function',
    agent_presets_resolve: typeof ctx.agentPresets?.resolve === 'function',
    agent_presets_mount: typeof ctx.agentPresets?.mount === 'function',
    agent_presets_composed: typeof ctx.agentPresets?.composedPreset === 'function',
    tools_guard: typeof ctx.tools?.guard === 'function',
    events_subscribe: typeof ctx.on === 'function',
  };
}

export class TaskBridge {
  constructor(ctx, { instanceId, allowExecution, token, warn }) {
    this.ctx = ctx;
    this.instanceId = instanceId;
    this.allowExecution = allowExecution;
    this.token = token;
    this.warn = warn;
    this.records = new Map();
    this.identities = new Set();
    this.receipts = new Map();
    this.sessions = new WeakMap();
    this.maintenanceTasks = new Set();
    this.maintenanceSessions = new Set();
    this.maintenancePending = new Set();
    this.active = null;
    this.closing = false;
    this.disposePromise = null;
    this.stopEvents = ctx.on('session/event', (session, event) => {
      const record = this.sessions.get(session);
      if (!record) return;
      try { this.observe(record, event); } catch { this.failObservation(record); }
    });
  }

  ready() {
    return this.allowExecution && !this.closing && Object.values(capabilities(this.ctx)).every(Boolean);
  }

  hello() {
    return {
      protocol: PROTOCOL, instance_id: this.instanceId, capabilities: capabilities(this.ctx),
      management_capabilities: managementCapabilities(this.ctx),
      execution_enabled: this.ready(), boundary_enforced: false,
      status: this.ready() ? 'ready' : 'execution_disabled',
      permission_profiles: ['dsh_native_v1'], permission_modes: [...MODES],
      budget_mode: 'request_count_and_cooperative_timeout', cost_hard_cap: false,
    };
  }

  checkIdentity(body) {
    requireValue(object(body));
    requireValue(body.instance_id === this.instanceId, 'instance_changed', 409);
    requireValue(typeof body.task_id === 'string' && ID.test(body.task_id));
  }

  get(body) {
    this.checkIdentity(body);
    const record = this.records.get(body.task_id);
    requireValue(record, this.identities.has(body.task_id) ? 'task_expired' : 'task_unknown', 404);
    return record;
  }

  async request(operation, body) {
    this.checkIdentity(body);
    requireValue(!this.closing, 'bridge_closing', 503);
    if (operation === 'status') {
      exact(body, ['instance_id', 'task_id']);
      return this.status(this.get(body));
    }
    if (operation === 'events') return this.events(body);
    requireValue(['create', 'send', 'cancel', 'close', 'maintain'].includes(operation), 'unknown_operation', 404);
    requireValue(typeof body.request_id === 'string' && ID.test(body.request_id));
    // Read-back must observe current durable state, never a cached mutation receipt.
    if (operation === 'maintain' && body.operation === 'status') return this.maintain(body);
    const hash = digest({ operation, body });
    const previous = this.receipts.get(body.request_id);
    if (previous) {
      requireValue(previous.hash === hash, 'request_conflict', 409);
      return previous.promise;
    }
    if (operation === 'create' || operation === 'send') {
      requireValue(this.ready(), 'execution_disabled', 403);
      requireValue(this.receipts.size < MAX_RECEIPTS / 2, 'receipt_capacity', 409);
    }
    if (operation === 'maintain') requireValue(this.receipts.size < MAX_RECEIPTS, 'receipt_capacity', 409);
    // Reserve identity synchronously before the first await. A failed or ambiguous
    // mutation keeps its receipt too. Cleanup remains possible when capacity is full.
    const promise = Promise.resolve().then(() => this[operation](body));
    if (this.receipts.size < MAX_RECEIPTS) this.receipts.set(body.request_id, { hash, promise });
    return promise;
  }

  add(record, type, fields = {}) {
    const event = {
      sequence: ++record.cursor, type, attempt_id: record.attempt?.id ?? null,
      actor: 'host', observed_at: new Date().toISOString(), ...fields,
    };
    const bytes = Buffer.byteLength(JSON.stringify(event));
    record.events.push({ event, bytes });
    record.eventBytes += bytes;
    while (record.events.length > MAX_EVENTS || record.eventBytes > MAX_EVENT_BYTES) {
      const removed = record.events.shift();
      record.eventBytes -= removed.bytes;
      record.floor = removed.event.sequence;
    }
    return event;
  }

  status(record) {
    return {
      instance_id: this.instanceId, task_id: record.id, session_id: record.sessionId,
      session_created_at: record.sessionCreatedAt ?? null,
      state: record.state, active_attempt_id: record.attempt?.active ? record.attempt.id : null,
      last_attempt_id: record.attempt?.id ?? null, agent_idle: record.agentIdle,
      cancel_requested: record.cancelRequested, close_requested: record.closing,
      dispose_confirmed: record.disposed, termination_confirmed: false,
      permission_profile: 'dsh_native_v1', permission_mode: record.permissionMode,
      agent_preset: record.agentPreset,
      approval_policy: record.approvalPolicy, boundary_enforced: false,
      usage: { ...record.usage }, event_cursor: record.cursor, floor_cursor: record.floor,
      last_error: record.error,
    };
  }

  events(body) {
    exact(body, ['instance_id', 'task_id', 'after_cursor'], ['limit']);
    requireValue(integer(body.after_cursor, 0, Number.MAX_SAFE_INTEGER));
    const limit = body.limit ?? 100;
    requireValue(integer(limit, 1, 100));
    const record = this.get(body);
    requireValue(body.after_cursor >= record.floor, 'events_expired', 409);
    requireValue(body.after_cursor <= record.cursor, 'cursor_ahead', 409);
    const events = [];
    let bytes = 0;
    for (const stored of record.events) {
      if (stored.event.sequence <= body.after_cursor) continue;
      if (events.length >= limit || (events.length && bytes + stored.bytes > PAGE_BYTES)) break;
      events.push(stored.event);
      bytes += stored.bytes;
    }
    return {
      instance_id: this.instanceId, task_id: record.id, events,
      next_cursor: events.at(-1)?.sequence ?? body.after_cursor,
      event_cursor: record.cursor, floor_cursor: record.floor, truncated: false,
    };
  }

  permission(record) {
    const sandbox = this.ctx.sandboxPolicy.resolve({ session: record.session });
    const approval = this.ctx.approval.effectivePolicy(record.session);
    requireValue(sandbox.mode === record.permissionMode && approval === 'never', 'permission_changed', 409);
    requireValue(typeof sandbox.workspaceRoot === 'string'
      && resolve(sandbox.workspaceRoot).toLowerCase() === record.cwd.toLowerCase(), 'workspace_changed', 409);
    return { mode: sandbox.mode, approval };
  }

  async create(body) {
    exact(body, ['instance_id', 'task_id', 'request_id', 'cwd', 'permission_mode',
      'permission_profile', 'native_permission_ack', 'max_output_tokens', 'max_turns', 'timeout_seconds']);
    requireValue(body.permission_profile === 'dsh_native_v1' && body.native_permission_ack === true,
      'native_permission_consent_required', 403);
    requireValue(MODES.has(body.permission_mode), 'permission_mode_unsupported', 403);
    requireValue(integer(body.max_output_tokens, 256, 8192)
      && integer(body.max_turns, 1, 8) && integer(body.timeout_seconds, 30, 1800));
    requireValue(typeof body.cwd === 'string' && body.cwd.length <= 1024
      && !/[\u0000-\u001f]/.test(body.cwd) && isAbsolute(body.cwd), 'invalid_workspace');
    requireValue(!this.maintenanceTasks.has(body.task_id), 'maintenance_busy', 409);
    requireValue(!this.identities.has(body.task_id), 'task_exists', 409);
    requireValue(this.identities.size < MAX_IDENTITIES, 'task_identity_capacity', 409);
    requireValue([...this.records.values()].filter((item) => !item.disposed).length < MAX_HANDLES,
      'session_capacity', 409);
    if (this.records.size >= MAX_RECORDS) {
      const old = [...this.records.values()].find((item) => item.disposed);
      requireValue(old, 'task_capacity', 409);
      this.records.delete(old.id);
    }
    const cwd = resolve(body.cwd);
    try {
      const info = await lstat(cwd);
      requireValue(info.isDirectory() && !info.isSymbolicLink(), 'invalid_workspace');
      requireValue((await realpath(cwd)).toLowerCase() === cwd.toLowerCase(), 'invalid_workspace');
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      throw new BridgeError('workspace_unavailable');
    }
    let model;
    try { model = this.ctx.agentDefaultModel.currentSelection(); } catch {
      throw new BridgeError('default_model_unavailable', 409);
    }
    requireValue(model && typeof model.provider === 'string' && model.provider.length > 0
      && typeof model.model === 'string' && model.model.length > 0, 'default_model_unavailable', 409);
    // NEXT keeps its ordinary tools in agent presets, not the host tool layer.
    // Resolve the current native default; never invent or mutate a deployment.
    const preparationDeadline = Date.now() + CREATION_MS;
    const agentPreset = await defaultAgentPreset(this.ctx);
    // Recheck after preparation awaits: simultaneous creates cannot reuse an ID.
    requireValue(Date.now() < preparationDeadline, 'agent_preset_unavailable', 409);
    requireValue(!this.maintenanceTasks.has(body.task_id), 'maintenance_busy', 409);
    requireValue(!this.identities.has(body.task_id), 'task_exists', 409);
    requireValue(!this.closing, 'bridge_closing', 503);
    requireValue(this.identities.size < MAX_IDENTITIES, 'task_identity_capacity', 409);
    requireValue([...this.records.values()].filter((item) => !item.disposed).length < MAX_HANDLES,
      'session_capacity', 409);
    if (this.records.size >= MAX_RECORDS) {
      const old = [...this.records.values()].find((item) => item.disposed);
      requireValue(old, 'task_capacity', 409);
      this.records.delete(old.id);
    }
    this.identities.add(body.task_id);
    const record = {
      id: body.task_id, sessionId: `alice-${randomUUID()}`, cwd, agentPreset,
      permissionMode: body.permission_mode, approvalPolicy: 'never',
      maxOutputTokens: body.max_output_tokens, maxTurns: body.max_turns,
      timeoutMs: body.timeout_seconds * 1000, state: 'unknown', error: null,
      cursor: 0, floor: 0, events: [], eventBytes: 0, handle: null, session: null,
      agent: null, agentIdle: false, attempt: null, attempts: new Set(), messages: new Set(),
      usage: emptyUsage(), cancelRequested: false, closing: false, disposed: false,
      idleTimer: null, closePromise: null, creationAbort: null,
    };
    this.records.set(record.id, record);
    const controller = new AbortController();
    record.creationAbort = controller;
    const timer = setTimeout(() => controller.abort(), Math.max(1, preparationDeadline - Date.now()));
    timer.unref();
    try {
      record.handle = await this.ctx.agents.create({
        sessionId: record.sessionId, meta: { cwd, agentPreset }, signal: controller.signal,
        agentOptions: { ...model, maxTokens: record.maxOutputTokens },
        setup: async (agentCtx, agent) => {
          record.agent = agent;
          record.session = agent.session;
          this.sessions.set(agent.session, record);
          this.ctx.permissionPresets.set(agent.session, record.permissionMode);
          setApprovalPolicy(agent.session, 'never');
          this.permission(record);
          agentCtx.on('agent/request', async (event, next) => {
            if (event.agent !== agent) return next();
            const attempt = record.attempt;
            if (!attempt?.active || record.closing || record.cancelRequested) {
              throw new BridgeError('request_not_admitted', 403);
            }
            this.permission(record);
            if (Date.now() >= attempt.deadline || attempt.requests >= record.maxTurns) {
              record.error = Date.now() >= attempt.deadline ? 'deadline_exceeded' : 'request_limit';
              throw new BridgeError(record.error, 409);
            }
            attempt.requests += 1;
            record.usage.model_requests = attempt.requests;
            record.usage.status = record.usage.reported_requests > 0 ? 'partial' : 'unknown';
            const config = await next();
            if (!attempt.active || record.cancelRequested || record.closing) throw new BridgeError('request_not_admitted', 403);
            this.permission(record);
            return { ...config, maxTokens: Math.min(
              Number.isSafeInteger(config.maxTokens) && config.maxTokens > 0 ? config.maxTokens : record.maxOutputTokens,
              record.maxOutputTokens,
            ) };
          });
          agentCtx.tools.guard((execution) => {
            if (BLOCKED_TOOLS.test(execution.name)) return 'Alice task sessions cannot manage plugins or delegate to other agents.';
            try { this.permission(record); } catch { return 'Alice task permission changed; execution is blocked.'; }
            if (!record.attempt?.active || record.cancelRequested || record.closing
                || Date.now() >= record.attempt.deadline) return 'Alice task is not active.';
            return undefined;
          });
          agentCtx.on('agent/inbox/claimed', ({ agent: claimant, message, turn }) => {
            if (claimant !== agent || !record.attempt?.active || message?.id !== record.attempt.dshMessageId) return;
            record.attempt.turn = turn;
            this.add(record, 'run_started', { message_id: record.attempt.messageId, actor: 'agent' });
          });
          agentCtx.on('agent/error', ({ agent: failed }) => {
            if (failed === agent && record.attempt?.active) record.error ??= 'agent_error';
          });
          try {
            const mounted = await this.ctx.agentPresets.mount(agentCtx, agentPreset);
            requireValue(mounted?.id === agentPreset
              && this.ctx.agentPresets.composedPreset(agentCtx) === agentPreset,
              'agent_preset_unavailable', 503);
          } catch {
            record.error = 'agent_preset_unavailable';
            throw new BridgeError('agent_preset_unavailable', 503);
          }
          // A preset contributes scoped tools/prompts. The task's native grant
          // remains exact after mounting and cannot inherit broader permission.
          this.permission(record);
        },
      });
      requireValue(record.handle.agent === record.agent && record.agent?.id === record.sessionId,
        'agent_identity_mismatch', 503);
      requireValue(!record.closing && !this.closing, 'creation_cancelled', 503);
      requireValue(this.ctx.agentPresets.composedPreset(record.agent.ctx) === agentPreset,
        'agent_preset_unavailable', 503);
      this.permission(record);
      const identity = await this.ctx.sessionPersistence.stat(record.sessionId, { signal: controller.signal });
      requireValue(identity?.header?.id === record.sessionId
        && identity.header.createdAt === record.session.header.createdAt
        && identity.header.cwd === record.cwd && identity.header.isSeeded === false
        && integer(identity.header.createdAt, 0, Number.MAX_SAFE_INTEGER),
      'session_identity_unavailable', 503);
      record.sessionCreatedAt = identity.header.createdAt;
      requireValue(!record.closing && !this.closing, 'creation_cancelled', 503);
      record.agentIdle = true;
      record.state = 'idle';
      this.add(record, 'task_created');
      this.armIdle(record);
      return this.status(record);
    } catch (error) {
      record.error = record.error === 'agent_preset_unavailable'
        || error instanceof BridgeError && error.code === 'agent_preset_unavailable'
        ? 'agent_preset_unavailable' : 'task_creation_failed';
      record.state = 'unknown';
      if (record.handle) this.beginClose(record);
      throw new BridgeError(record.error, 503);
    } finally {
      clearTimeout(timer);
      record.creationAbort = null;
    }
  }

  send(body) {
    exact(body, ['instance_id', 'task_id', 'request_id', 'attempt_id', 'message_id', 'text']);
    requireValue(typeof body.attempt_id === 'string' && ID.test(body.attempt_id)
      && typeof body.message_id === 'string' && ID.test(body.message_id));
    requireValue(typeof body.text === 'string' && body.text.trim().length > 0
      && Buffer.byteLength(body.text) <= 64 * 1024, 'invalid_text');
    const record = this.get(body);
    requireValue(!this.maintenanceTasks.has(record.id)
      && !this.maintenanceSessions.has(record.sessionId), 'maintenance_busy', 409);
    requireValue(record.handle && !record.disposed && !record.closing, 'task_closed', 409);
    requireValue(record.agentIdle && !record.attempt?.active && record.agent.status === 'idle', 'task_busy', 409);
    requireValue(!this.active, 'bridge_busy', 409);
    requireValue(!record.attempts.has(body.attempt_id) && !record.messages.has(body.message_id), 'message_conflict', 409);
    this.permission(record);
    clearTimeout(record.idleTimer);
    const message = createUserMessage({ content: [{ type: 'text', text: body.text }], source: { kind: 'user' } });
    const attempt = {
      id: body.attempt_id, messageId: body.message_id, dshMessageId: message.id,
      active: true, requests: 0, turn: null, outcome: null, failure: false,
      deadline: Date.now() + record.timeoutMs, timer: null, stopTimer: null,
      settled: new Set(), toolCalls: new Map(),
    };
    record.attempt = attempt;
    record.attempts.add(attempt.id);
    record.messages.add(body.message_id);
    record.usage = emptyUsage();
    record.error = null;
    record.cancelRequested = false;
    record.agentIdle = false;
    record.state = 'running';
    this.active = record;
    this.add(record, 'alice_message_sent', {
      actor: 'alice', message_id: body.message_id, ...publicText(body.text, this.token),
    });
    try {
      record.agent.followup(message);
    } catch {
      record.error = 'send_unknown';
      record.state = 'unknown';
      this.cancelOwned(record, 'send_unknown');
      this.waitIdle(record, attempt);
      throw new BridgeError('send_unknown', 503);
    }
    attempt.timer = setTimeout(() => this.cancelOwned(record, 'deadline_exceeded'), record.timeoutMs);
    attempt.timer.unref();
    this.waitIdle(record, attempt);
    return {
      instance_id: this.instanceId, task_id: record.id, session_id: record.sessionId,
      accepted: true, dsh_message_id: message.id, state: record.state, event_cursor: record.cursor,
    };
  }

  waitIdle(record, attempt) {
    const agent = record.agent;
    Promise.resolve().then(() => agent.whenIdle()).then(() => {
      if (record.disposed || record.attempt !== attempt || !attempt.active) return;
      attempt.active = false;
      clearTimeout(attempt.timer);
      clearTimeout(attempt.stopTimer);
      record.agentIdle = true;
      if (this.active === record) this.active = null;
      const completed = attempt.outcome === 'completed' && !record.error && !record.cancelRequested;
      record.state = completed ? 'succeeded' : record.cancelRequested && !record.error ? 'cancelled' : 'failed';
      if (!completed && !record.error && !record.cancelRequested) record.error = attempt.outcome === 'max-tokens' ? 'output_limit' : 'run_not_completed';
      this.add(record, completed ? 'run_completed' : record.cancelRequested ? 'run_stopped' : 'run_failed', {
        agent_idle: true, termination_confirmed: false,
        ...(record.error ? { error: record.error } : {}),
      });
      this.armIdle(record);
    }, () => {
      if (record.disposed || record.attempt !== attempt || !attempt.active) return;
      record.state = 'unknown';
      record.error = 'idle_unconfirmed';
      this.add(record, 'run_failed', { error: record.error, agent_idle: false, termination_confirmed: false });
      // Do not release the instance slot on an unconfirmed driver failure.
    });
  }

  observe(record, event) {
    const attempt = record.attempt;
    if (!attempt?.active || !event || !Number.isSafeInteger(event.seq)) return;
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if (event.type === 'turn/end' && data.turn === attempt.turn) attempt.outcome = data.reason?.kind ?? 'unknown';
    if (data.turn !== attempt.turn) return;
    if (event.type === 'assistant/message' || event.type === 'assistant/attempt') {
      if (attempt.settled.has(event.seq)) return;
      attempt.settled.add(event.seq);
      const rawUsage = data.usage ?? (Array.isArray(data.stream) ? lastAssistantStreamChunk(data.stream, 'usage')?.usage : undefined);
      const sample = usageSample(rawUsage);
      if (sample) {
        const usage = record.usage;
        usage.reported_requests += 1;
        for (const [key, value] of Object.entries(sample)) usage[key] = (usage[key] ?? 0) + value;
        usage.total_tokens = usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens;
        usage.status = usage.reported_requests === usage.model_requests ? 'reported' : 'partial';
        this.add(record, 'usage_updated', { actor: 'agent', usage: { ...usage } });
      } else if (record.usage.reported_requests > 0) record.usage.status = 'partial';
      const finish = Array.isArray(data.stream) ? lastAssistantStreamChunk(data.stream, 'finish')?.reason?.kind : undefined;
      if (event.type === 'assistant/message' && data.interrupted !== true
          && !['max-tokens', 'aborted', 'error'].includes(finish)
          && data.message?.interrupted !== true && data.message?.role === 'assistant'
          && Array.isArray(data.message.content)) {
        const text = data.message.content.filter((block) => block?.type === 'text' && typeof block.text === 'string')
          .map((block) => block.text).join('\n');
        if (text) this.add(record, 'agent_message_completed', {
          actor: 'agent', message_id: `${record.sessionId}:${event.seq}`, ...publicText(text, this.token),
        });
      }
    } else if (event.type === 'tool/call') {
      if (typeof data.callId !== 'string') return;
      if (attempt.toolCalls.size >= 512) { this.cancelOwned(record, 'event_limit'); return; }
      const name = toolName(data.name);
      attempt.toolCalls.set(data.callId, name);
      this.add(record, 'tool_started', { actor: 'agent', tool_name: name });
    } else if (event.type === 'tool/result') {
      const callId = data.message?.toolCallId;
      if (typeof callId !== 'string') return;
      const name = attempt.toolCalls.get(callId);
      if (name === undefined) return;
      attempt.toolCalls.delete(callId);
      this.add(record, data.message.isError === true ? 'tool_failed' : 'tool_completed', {
        actor: 'agent', tool_name: name,
      });
    }
  }

  failObservation(record) {
    if (record.attempt?.active) this.cancelOwned(record, 'event_projection_failed');
  }

  cancelOwned(record, reason = null) {
    if (reason) record.error ??= reason;
    if (!record.cancelRequested) {
      record.cancelRequested = true;
      this.add(record, 'cancel_requested');
    }
    if (!record.agent || record.disposed) return;
    try { record.agent.cancel({ kind: 'user' }); } catch {
      record.state = 'unknown';
      record.error = 'cancel_unconfirmed';
    }
    if (record.attempt?.active && !record.attempt.stopTimer) {
      record.attempt.stopTimer = setTimeout(() => {
        if (!record.attempt?.active) return;
        record.state = 'unknown';
        record.error ??= 'cancel_unconfirmed';
        this.add(record, 'run_failed', { error: record.error, agent_idle: false, termination_confirmed: false });
      }, STOP_WAIT_MS);
      record.attempt.stopTimer.unref();
    }
  }

  cancel(body) {
    exact(body, ['instance_id', 'task_id', 'request_id']);
    const record = this.get(body);
    this.cancelOwned(record);
    if (record.agentIdle && !record.closing && !record.disposed) record.state = 'cancelled';
    return { ...this.status(record), accepted: true };
  }

  beginClose(record) {
    if (record.closePromise) return record.closePromise;
    record.closing = true;
    record.creationAbort?.abort();
    clearTimeout(record.idleTimer);
    this.cancelOwned(record);
    if (!record.handle) {
      record.state = 'unknown';
      record.error = 'creation_cancelled';
      return Promise.resolve();
    }
    this.add(record, 'close_requested');
    record.closePromise = Promise.resolve().then(() => record.handle?.dispose()).then(() => {
      if (!record.handle) throw new BridgeError('dispose_unconfirmed', 503);
      record.disposed = true;
      record.agentIdle = true;
      record.state = 'closed';
      this.sessions.delete(record.session);
      record.session = null;
      record.agent = null;
      record.handle = null;
      if (record.attempt) {
        record.attempt.active = false;
        clearTimeout(record.attempt.timer);
        clearTimeout(record.attempt.stopTimer);
      }
      if (this.active === record) this.active = null;
      this.add(record, 'task_closed', { dispose_confirmed: true, termination_confirmed: false });
    }).catch(() => {
      record.state = 'unknown';
      record.error = 'dispose_unconfirmed';
      this.add(record, 'run_failed', { error: record.error, termination_confirmed: false });
    });
    return record.closePromise;
  }

  close(body) {
    exact(body, ['instance_id', 'task_id', 'request_id']);
    const record = this.get(body);
    this.beginClose(record);
    return { ...this.status(record), accepted: true };
  }

  async maintain(body) {
    const controller = new AbortController();
    let timer;
    const pending = maintainSession(this, body, controller.signal);
    this.maintenancePending.add(pending);
    // An uninterruptible native archive write may outlive the HTTP timeout.
    // Retain its lock and lifecycle lease until the actual operation settles.
    pending.finally(() => this.maintenancePending.delete(pending)).catch(() => {});
    try {
      return await Promise.race([
        pending,
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new BridgeError('maintenance_unconfirmed', 503));
          }, CREATION_MS);
          timer.unref();
        }),
      ]);
    } catch (error) {
      if (error instanceof MaintenanceError) throw new BridgeError(error.code, error.status);
      if (error instanceof BridgeError) throw error;
      throw new BridgeError('maintenance_unconfirmed', 503);
    } finally {
      clearTimeout(timer);
    }
  }

  armIdle(record) {
    clearTimeout(record.idleTimer);
    if (record.closing || record.disposed || !record.agentIdle) return;
    record.idleTimer = setTimeout(() => this.beginClose(record), IDLE_MS);
    record.idleTimer.unref();
  }

  dispose() {
    if (this.disposePromise) return this.disposePromise;
    this.closing = true;
    this.disposePromise = this.disposeOwned();
    return this.disposePromise;
  }

  cleanupConfirmed() {
    return this.closing && this.maintenancePending.size === 0
      && [...this.records.values()].every((record) => record.disposed);
  }

  async disposeOwned() {
    const pending = [...this.maintenancePending];
    for (const record of this.records.values()) {
      clearTimeout(record.idleTimer);
      if (!record.disposed) pending.push(this.beginClose(record));
    }
    let timer;
    const done = await Promise.race([
      Promise.allSettled(pending).then(() => true),
      new Promise((resolveDone) => { timer = setTimeout(() => resolveDone(false), STOP_WAIT_MS); }),
    ]);
    clearTimeout(timer);
    if (!done || this.maintenancePending.size > 0
        || [...this.records.values()].some((record) => !record.disposed)) {
      this.warn('Alice bridge task cleanup is unconfirmed; shared DSH host was not stopped.');
    }
    this.stopEvents();
    this.token = '';
  }
}
