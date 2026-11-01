import { win32 } from 'node:path';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SESSION = /^alice-([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/;
const TASK = /^[0-9a-f]{32}$/;
const REQUEST = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const OPERATIONS = new Set(['archive', 'restore', 'delete', 'status']);
const REQUIRED = ['instance_id', 'request_id', 'task_id', 'operation', 'session_id',
  'origin_instance', 'cwd', 'created_after', 'created_before'];

export class MaintenanceError extends Error {
  constructor(code, status = 400) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

function requireValue(value, code = 'invalid_request', status = 400) {
  if (!value) throw new MaintenanceError(code, status);
}

function integer(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function cwdKey(value) {
  requireValue(typeof value === 'string' && value.length <= 1024
    && !/[\u0000-\u001f]/.test(value) && /^[A-Za-z]:[\\/]/.test(value),
  'session_identity_mismatch', 409);
  // Compare immutable path spellings, never visit a historical task directory.
  return win32.normalize(value).replace(/[\\/]+$/, '').toLowerCase();
}

export function managementCapabilities(ctx) {
  const read = typeof ctx.sessionPersistence?.stat === 'function'
    && typeof ctx.sessions?.get === 'function' && typeof ctx.agents?.get === 'function';
  return {
    archive: read && typeof ctx.workspaceRegistry?.archiveSession === 'function',
    restore: read && typeof ctx.workspaceRegistry?.unarchiveSession === 'function',
    delete: false,
  };
}

function validate(body) {
  requireValue(body && typeof body === 'object' && !Array.isArray(body)
    && Object.getPrototypeOf(body) === Object.prototype
    && REQUIRED.every((key) => Object.hasOwn(body, key))
    && Object.keys(body).every((key) => REQUIRED.includes(key) || key === 'session_created_at'));
  requireValue(UUID.test(body.instance_id) && UUID.test(body.origin_instance)
    && TASK.test(body.task_id) && REQUEST.test(body.request_id)
    && SESSION.test(body.session_id) && OPERATIONS.has(body.operation));
  requireValue(integer(body.created_after) && integer(body.created_before)
    && body.created_after <= body.created_before);
  if (Object.hasOwn(body, 'session_created_at')) {
    requireValue(integer(body.session_created_at)
      && body.session_created_at >= body.created_after && body.session_created_at <= body.created_before);
  }
  cwdKey(body.cwd);
}

function checkHeader(header, body) {
  requireValue(header && header.version === 4 && header.id === body.session_id
    && integer(header.createdAt) && header.createdAt >= body.created_after
    && header.createdAt <= body.created_before
    && (body.session_created_at === undefined || header.createdAt === body.session_created_at)
    && header.isSeeded === false && header.parentSession === undefined
    && header.origin === undefined
    && (header.delegationDepth === undefined || header.delegationDepth === 0)
    && cwdKey(header.cwd) === cwdKey(body.cwd), 'session_identity_mismatch', 409);
}

function archived(ctx, id) {
  const ids = ctx.workspaceRegistry.archivedSessionIds;
  requireValue(Array.isArray(ids), 'maintenance_unavailable', 503);
  // Return only the selected target's bit, never other sessions' identities.
  return ids.includes(id);
}

function liveAgent(ctx, body) {
  const session = ctx.sessions.get(body.session_id);
  if (session !== undefined) checkHeader(session.header, body);
  const agent = ctx.agents.get(body.session_id);
  if (agent !== undefined) {
    requireValue(agent.id === body.session_id && agent.session === session,
      'session_identity_mismatch', 409);
  }
  return agent;
}

function response(bridge, body, header, record) {
  const isArchived = archived(bridge.ctx, body.session_id);
  const agent = liveAgent(bridge.ctx, body);
  return {
    instance_id: bridge.instanceId, task_id: body.task_id, session_id: body.session_id,
    operation: body.operation, status: isArchived ? 'archived' : 'restored', archived: isArchived,
    session_created_at: header.createdAt, agent_idle: agent === undefined || agent.status === 'idle',
    dispose_confirmed: record?.disposed === true, history_deleted: false, model_invoked: false,
  };
}

// Only the authenticated Alice host can supply this ledger attestation. The
// original instance is not a DSH header field and is not a cryptographic proof.
// No lookup below enumerates sessions, reads event bodies, or resumes an agent.
export async function maintainSession(bridge, body, signal) {
  validate(body);
  requireValue(body.instance_id === bridge.instanceId, 'instance_changed', 409);
  requireValue(!bridge.closing, 'bridge_closing', 503);
  if (body.operation === 'delete') throw new MaintenanceError('dsh_delete_unsupported', 501);
  const available = managementCapabilities(bridge.ctx);
  requireValue(body.operation === 'status' ? available.archive && available.restore
    : available[body.operation], 'maintenance_unavailable', 503);
  requireValue(!bridge.maintenanceTasks.has(body.task_id)
    && !bridge.maintenanceSessions.has(body.session_id), 'maintenance_busy', 409);
  requireValue(bridge.maintenanceTasks.size < 4, 'maintenance_busy', 409);
  bridge.maintenanceTasks.add(body.task_id);
  bridge.maintenanceSessions.add(body.session_id);
  let effectsStarted = false;
  try {
    const record = bridge.records.get(body.task_id);
    const sameSession = [...bridge.records.values()].find((item) => item.sessionId === body.session_id);
    requireValue(!sameSession || sameSession === record, 'session_identity_mismatch', 409);
    if (record) {
      requireValue(record.sessionId === body.session_id && body.origin_instance === bridge.instanceId
        && cwdKey(record.cwd) === cwdKey(body.cwd), 'session_identity_mismatch', 409);
    }
    signal.throwIfAborted();
    const stored = await bridge.ctx.sessionPersistence.stat(body.session_id, { signal });
    requireValue(stored !== undefined, 'session_not_found', 404);
    checkHeader(stored.header, body);
    // A verified historical identity must not later be recycled by a concurrent
    // create whose preset resolution started before this maintenance request.
    if (!bridge.identities.has(body.task_id)) {
      requireValue(bridge.identities.size < 4096, 'task_identity_capacity', 409);
      bridge.identities.add(body.task_id);
    }
    let agent = liveAgent(bridge.ctx, body);
    const target = body.operation === 'archive';
    if (body.operation === 'status' || archived(bridge.ctx, body.session_id) === target) {
      return response(bridge, body, stored.header, record);
    }
    requireValue(!agent || agent.status === 'idle', 'session_active', 409);
    if (record && !record.disposed) {
      requireValue(record.agentIdle && !record.attempt?.active && !record.closing
        && record.handle && agent === record.agent, 'session_active', 409);
      effectsStarted = true;
      await bridge.beginClose(record);
      requireValue(record.disposed, 'dispose_unconfirmed', 503);
    }
    signal.throwIfAborted();
    requireValue(!bridge.closing, 'bridge_closing', 503);
    agent = liveAgent(bridge.ctx, body);
    requireValue(!agent || agent.status === 'idle', 'session_active', 409);
    effectsStarted = true;
    try {
      if (target) await bridge.ctx.workspaceRegistry.archiveSession(body.session_id, { stopActivity: false });
      else await bridge.ctx.workspaceRegistry.unarchiveSession(body.session_id);
    } catch (error) {
      if (error?.name === 'WorkspaceActiveSessionError') throw new MaintenanceError('session_active', 409);
      if (error?.name === 'WorkspaceUnknownSessionError') throw new MaintenanceError('session_not_found', 404);
      throw error;
    }
    // The public methods resolve after durability. Re-read both target identity
    // and the archive bit; a vanished target is never reported as deleted.
    const after = await bridge.ctx.sessionPersistence.stat(body.session_id, { signal });
    requireValue(after !== undefined, 'session_not_found', 404);
    checkHeader(after.header, body);
    requireValue(after.header.createdAt === stored.header.createdAt
      && archived(bridge.ctx, body.session_id) === target, 'maintenance_unconfirmed', 503);
    return response(bridge, body, after.header, record);
  } catch (error) {
    // Even a later 404/409 cannot undo a disposed handle or a durable archive
    // write. Keep Alice's pending intent until an explicit target read-back.
    if (effectsStarted) throw new MaintenanceError(
      error instanceof MaintenanceError && error.code === 'dispose_unconfirmed'
        ? 'dispose_unconfirmed' : 'maintenance_unconfirmed', 503);
    throw error;
  } finally {
    bridge.maintenanceTasks.delete(body.task_id);
    bridge.maintenanceSessions.delete(body.session_id);
  }
}
