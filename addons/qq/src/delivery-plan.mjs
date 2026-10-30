import { createHash } from 'node:crypto';

const digest = value => createHash('sha256').update(value).digest('hex');
const HASH = /^[a-f0-9]{64}$/;
const UNSENT = new Set(['ready', 'retry_wait']);
const REASONS = new Set([null, 'capacity', 'protected_block', 'oversize_grapheme']);
const fail = () => { throw new Error('delivery_plan_invalid'); };

// Sorting every object level makes snapshots independent of property insertion order.
function canonical(value, depth = 0) {
  if (depth > 12) return fail();
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map(v => canonical(v, depth + 1));
  if (value && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k], depth + 1)]));
  }
  return fail();
}

function planId(record, plan) {
  return digest(JSON.stringify(canonical([
    plan.kind, record.session_epoch, record.request_id, plan.content_hash,
    plan.planner_version, plan.policy_snapshot, record.segments.map(s => s.hash),
    plan.truncated, plan.reason, plan.hard_split,
  ])));
}

function deepFreeze(value) {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

function lockPlan(record) {
  for (const segment of record.segments) {
    for (const key of ['index', 'seq', 'hash']) {
      Object.defineProperty(segment, key, { value: segment[key], enumerable: true, writable: false, configurable: false });
    }
  }
  Object.freeze(record.segments); // Segment states remain mutable; membership and order do not.
  Object.defineProperty(record, 'segments', { value: record.segments, enumerable: true, writable: false, configurable: false });
  Object.defineProperty(record, 'plan', { value: deepFreeze(record.plan), enumerable: true, writable: false, configurable: false });
  return record.plan;
}

function validText(text) {
  return typeof text === 'string' && text.length > 0 && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(text)
    && [...text].length <= 1000 && Buffer.byteLength(text, 'utf8') <= 4000;
}

function validPolicy(policy, plannerVersion, segmentCount) {
  return policy && typeof policy === 'object' && !Array.isArray(policy)
    && ['natural', 'length'].includes(policy.mode) && plannerVersion === `${policy.mode}-v1`
    && Number.isInteger(policy.max_reply_segments) && policy.max_reply_segments >= segmentCount && policy.max_reply_segments <= 3
    && policy.max_codepoints === 1000 && policy.max_utf8_bytes === 4000
    && Number.isInteger(policy.send_spacing_ms) && policy.send_spacing_ms >= 1000 && policy.send_spacing_ms <= 60000;
}

export function freezePlan(record, spec, contentHash) {
  // Even an incomplete existing plan is evidence: never overwrite it by re-planning.
  if ('segments' in record || 'plan' in record) throw new Error('delivery_plan_already_frozen');
  if (!spec || !Array.isArray(spec.parts) || spec.parts.length < 1 || spec.parts.length > 3 ||
      spec.parts.some(text => !validText(text)) || !HASH.test(contentHash ?? '') ||
      !['chat', 'notice'].includes(record.kind) || (record.kind === 'notice' && spec.parts.length !== 1) ||
      (record.kind === 'chat' && record.reply_hash !== contentHash) ||
      !['natural-v1', 'length-v1'].includes(spec.planner_version) ||
      typeof spec.truncated !== 'boolean' || typeof spec.hard_split !== 'boolean' ||
      !REASONS.has(spec.reason) ||
      spec.truncated !== (spec.reason !== null) ||
      !validPolicy(spec.policy_snapshot, spec.planner_version, spec.parts.length)) fail();
  const segments = spec.parts.map((text, index) => ({
    index, seq: index + 1, text, hash: digest(text), status: 'ready', attempts: 0,
    dispatch_attempts: 0, auth_refresh_used: false, rate_retry_count: 0,
  }));
  const plan = {
    version: 1, kind: record.kind, origin: 'new', planner_version: spec.planner_version,
    policy_snapshot: canonical(spec.policy_snapshot), segment_count: segments.length,
    truncated: spec.truncated, reason: spec.reason, hard_split: spec.hard_split, content_hash: contentHash,
  };
  // Compute first; a malformed snapshot must not leave half a plan on the record.
  plan.plan_id = planId({ ...record, segments }, plan);
  record.segments = segments;
  record.plan = plan;
  return lockPlan(record);
}

export function stopUnsent(record, reason) {
  if (typeof reason !== 'string' || !/^[a-z_]{1,64}$/.test(reason)) throw new Error('invalid_stop_reason');
  for (const segment of record.segments ?? []) {
    if (!UNSENT.has(segment.status)) continue;
    segment.status = 'failed';
    segment.error_code = reason;
    delete segment.text;
  }
}

export function adoptLegacyPlan(record) {
  if (!record.segments) return;
  if ('plan' in record || !record.segments.length || record.segments.length > 3) fail();
  const contentHash = record.kind === 'chat' ? record.reply_hash
    : record.segments.length === 1 ? record.segments[0].hash : digest(JSON.stringify(record.segments.map(s => s.hash)));
  if (!HASH.test(contentHash ?? '')) fail();
  for (const segment of record.segments) {
    // Old attempts include receipt retries. They are an upper bound, never reset to zero.
    segment.dispatch_attempts = segment.attempts ?? 0;
    segment.auth_refresh_used = segment.dispatch_attempts > 0;
    segment.rate_retry_count = Math.min(2, segment.dispatch_attempts);
  }
  record.plan = {
    version: 1, kind: record.kind, origin: 'legacy', planner_version: 'legacy-frozen',
    policy_snapshot: null, segment_count: record.segments.length, truncated: null,
    reason: 'legacy_unknown', hard_split: null, content_hash: contentHash,
  };
  record.plan.plan_id = planId(record, record.plan);
}

export function validatePlan(record) {
  const p = record.plan;
  if (!p || !record.segments || p.version !== 1 || p.kind !== record.kind ||
      !['new', 'legacy'].includes(p.origin) || !HASH.test(p.content_hash ?? '') || !HASH.test(p.plan_id ?? '') ||
      p.segment_count !== record.segments.length || p.segment_count < 1 || p.segment_count > 3 ||
      (record.kind === 'chat' && record.reply_hash !== p.content_hash)) fail();
  if (p.origin === 'legacy') {
    if (p.planner_version !== 'legacy-frozen' || p.policy_snapshot !== null || p.truncated !== null ||
        p.reason !== 'legacy_unknown' || p.hard_split !== null) fail();
  } else if (!['natural-v1', 'length-v1'].includes(p.planner_version) ||
      !validPolicy(p.policy_snapshot, p.planner_version, p.segment_count) ||
      typeof p.truncated !== 'boolean' || typeof p.hard_split !== 'boolean' ||
      !REASONS.has(p.reason) || p.truncated !== (p.reason !== null) ||
      (record.kind === 'notice' && p.segment_count !== 1)) fail();
  if (planId(record, p) !== p.plan_id) fail();
  for (const [index, s] of record.segments.entries()) {
    if (s.index !== index || s.seq !== index + 1 || !HASH.test(s.hash ?? '') ||
        !Number.isSafeInteger(s.attempts) || s.attempts < 0 ||
        !Number.isSafeInteger(s.dispatch_attempts) || s.dispatch_attempts < 0 ||
        typeof s.auth_refresh_used !== 'boolean' || !Number.isSafeInteger(s.rate_retry_count) ||
        s.rate_retry_count < 0 || s.rate_retry_count > 2 ||
        (typeof s.text !== 'undefined' && (!validText(s.text) || digest(s.text) !== s.hash))) fail();
  }
  return lockPlan(record);
}
