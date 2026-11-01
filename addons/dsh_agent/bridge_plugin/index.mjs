import { createServer } from 'node:http';
import { randomUUID, timingSafeEqual } from 'node:crypto';
import { BridgeError, TaskBridge } from './business.mjs';
import { inspectManagedCredential, readManagedToken, TOKEN_PATTERN } from './credential.mjs';

export const name = 'alice-dsh-bridge-probe';
export const inject = ['agents', 'sessions', 'permissionPresets', 'sandboxPolicy', 'approval', 'agentDefaultModel', 'agentPresets', 'tools', 'sessionPersistence', 'workspaceRegistry'];

const PREFIX = '/alice-bridge/v1/';
const MAX_CONNECTIONS = 8;
const MAX_BODY_BYTES = 128 * 1024;
const HTTP_TIMEOUT_MS = 15000;
const LIFECYCLE_LEASE = Symbol.for('alice-dsh-bridge.lifecycle.v1');
const MANAGED_SEEN = Symbol.for('alice-dsh-bridge.managed-seen.v1');

function warning(ctx, message) {
  try {
    if (typeof ctx.logger?.warn === 'function') ctx.logger.warn(message);
  } catch { /* A diagnostic sink must not crash the shared host. */ }
}

function configuration(config) {
  if (config === undefined) return { enabled: false, allow_execution: false, port: 18765 };
  if (config === null || typeof config !== 'object' || Array.isArray(config)
      || Object.getPrototypeOf(config) !== Object.prototype
      || Object.keys(config).some((key) => !['enabled', 'allow_execution', 'port'].includes(key))) return null;
  const enabled = config.enabled ?? false;
  const allow_execution = config.allow_execution ?? false;
  const port = config.port ?? 18765;
  if (typeof enabled !== 'boolean' || typeof allow_execution !== 'boolean' || port !== 18765) return null;
  return { enabled, allow_execution, port };
}

function headerNames(request) {
  const counts = new Map();
  for (let index = 0; index < request.rawHeaders.length; index += 2) {
    const key = request.rawHeaders[index].toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

function authenticated(header, token) {
  if (typeof header !== 'string' || !header.startsWith('Bearer ')) return false;
  const value = header.slice(7);
  if (!TOKEN_PATTERN.test(value)) return false;
  const supplied = Buffer.from(value, 'ascii');
  return supplied.length === token.length && timingSafeEqual(supplied, token);
}

function send(response, status, value) {
  if (response.destroyed || response.writableEnded) return;
  const body = Buffer.from(JSON.stringify(value), 'utf8');
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Connection': 'close',
  });
  response.end(body);
}

function readBody(request, length) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > length || size > MAX_BODY_BYTES) {
        reject(new BridgeError('body_too_large', 413));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.once('aborted', () => reject(new BridgeError('request_aborted', 400)));
    request.once('error', () => reject(new BridgeError('request_aborted', 400)));
    request.once('end', () => {
      if (size !== length) { reject(new BridgeError('invalid_body', 400)); return; }
      try {
        const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
        resolve(JSON.parse(text));
      } catch { reject(new BridgeError('invalid_json', 400)); }
    });
  });
}

export function apply(ctx, config) {
  const selected = configuration(config);
  if (selected === null) {
    warning(ctx, 'Alice bridge remains disabled: invalid configuration.');
    return;
  }
  if (!selected.enabled) return;
  ctx.effect(() => {
    const abort = new AbortController();
    const manualToken = process.env.ALICE_DSH_BRIDGE_TOKEN;
    let current = null;
    let managedSeen = globalThis[MANAGED_SEEN] === true;
    let previousFingerprint = null;
    let disposed = false;
    let blocked = false;
    let timer;
    let pending = Promise.resolve();

    async function stopCurrent() {
      const old = current;
      current = null;
      if (!old) return;
      await old.stop();
      if (!old.cleanupConfirmed()) {
        blocked = true;
        warning(ctx, 'Alice bridge credential change is blocked: previous task cleanup is unconfirmed.');
      }
    }

    async function refresh() {
      const snapshot = await inspectManagedCredential();
      if (disposed) return;
      if (snapshot.kind === 'present' || snapshot.kind === 'invalid') {
        managedSeen = true;
        globalThis[MANAGED_SEEN] = true;
      }
      // Another plugin apply may have observed a managed credential first.
      managedSeen ||= globalThis[MANAGED_SEEN] === true;
      const fingerprint = `${managedSeen ? 'managed' : 'unmanaged'}:${snapshot.kind}:${snapshot.fingerprint}`;
      if (fingerprint === previousFingerprint) return;
      previousFingerprint = fingerprint;
      // Drop the old listener before reading/decrypting a new credential. A
      // failed cleanup never permits a second instance to admit work.
      await stopCurrent();
      if (disposed || blocked) return;
      let tokenValue;
      if (snapshot.kind === 'present') {
        try { tokenValue = await readManagedToken(snapshot, abort.signal); } catch {
          if (!disposed) warning(ctx, 'Alice bridge remains disabled: managed credential could not be opened.');
          return;
        }
      } else if (!managedSeen && globalThis[MANAGED_SEEN] !== true
          && ['missing', 'unsupported'].includes(snapshot.kind)
          && typeof manualToken === 'string' && TOKEN_PATTERN.test(manualToken)) {
        tokenValue = manualToken;
      } else {
        if (snapshot.kind === 'invalid') warning(ctx, 'Alice bridge remains disabled: managed credential is invalid.');
        return;
      }
      if (!disposed && !blocked) current = startBridge(ctx, selected, tokenValue);
    }

    function schedule() {
      pending = refresh().catch(() => {
        blocked = true;
        warning(ctx, 'Alice bridge remains disabled: credential lifecycle could not be confirmed.');
        return stopCurrent();
      }).finally(() => {
        if (!disposed && !blocked) {
          timer = setTimeout(schedule, 2000);
          timer.unref();
        }
      });
    }
    schedule();
    return async () => {
      disposed = true;
      abort.abort();
      clearTimeout(timer);
      await pending;
      await stopCurrent();
    };
  }, 'alice-bridge: managed credential and owned task sessions');
}

function startBridge(ctx, selected, tokenValue) {
  const previous = globalThis[LIFECYCLE_LEASE];
  if (previous && !(previous.listenerStopped === true && previous.bridge?.cleanupConfirmed() === true)) {
    throw new BridgeError('previous_cleanup_unconfirmed', 503);
  }
  // This synchronous claim survives module/apply reloads. Never erase an
  // unknown old agent simply because its HTTP port has already been closed.
  const lease = { owner: Symbol('alice-bridge-owner'), bridge: null, listenerStopped: false };
  globalThis[LIFECYCLE_LEASE] = lease;
  const token = Buffer.from(tokenValue, 'ascii');
  const expectedHost = `127.0.0.1:${selected.port}`;
    let bridge;
    try {
      bridge = new TaskBridge(ctx, {
        instanceId: randomUUID(), allowExecution: selected.allow_execution, token: tokenValue,
        warn: (message) => warning(ctx, message),
      });
    } catch {
      // No listener or task exists before this constructor returns.
      token.fill(0);
      if (globalThis[LIFECYCLE_LEASE] === lease) delete globalThis[LIFECYCLE_LEASE];
      throw new BridgeError('bridge_unavailable', 503);
    }
    lease.bridge = bridge;
    const sockets = new Set();
    let accepting = false;
    let disposed = false;
    let closeTask;
    const listenAbort = new AbortController();
    const server = createServer({
      maxHeaderSize: 4096, requestTimeout: HTTP_TIMEOUT_MS, headersTimeout: 3000, keepAliveTimeout: 1000,
    }, (request, response) => {
      request.on('error', () => {});
      response.on('error', () => {});
      void (async () => {
        const headers = headerNames(request);
        const browserSource = [...headers.keys()].some((key) =>
          key === 'origin' || key === 'referer' || key.startsWith('sec-fetch-'));
        if (!accepting || disposed || request.httpVersion !== '1.1'
            || headers.get('host') !== 1 || headers.get('authorization') !== 1
            || request.headers.host !== expectedHost || browserSource
            || ['transfer-encoding', 'expect', 'trailer'].some((key) => headers.has(key))) {
          throw new BridgeError('request_rejected', 400);
        }
        if (!authenticated(request.headers.authorization, token)) throw new BridgeError('authentication_required', 401);
        if (request.method === 'GET' && request.url === `${PREFIX}hello`) {
          if (headers.has('content-length') || headers.has('content-type')) throw new BridgeError('request_rejected', 400);
          send(response, 200, bridge.hello());
          return;
        }
        const operation = /^\/alice-bridge\/v1\/tasks\/(create|send|status|events|cancel|close|maintain)$/.exec(request.url ?? '')?.[1];
        if (request.method !== 'POST' || !operation) throw new BridgeError('unknown_endpoint', 404);
        const lengthText = request.headers['content-length'];
        if (headers.get('content-length') !== 1 || headers.get('content-type') !== 1
            || request.headers['content-type'] !== 'application/json'
            || typeof lengthText !== 'string' || !/^[1-9][0-9]{0,5}$/.test(lengthText)) {
          throw new BridgeError('invalid_envelope', 400);
        }
        const length = Number(lengthText);
        if (length > MAX_BODY_BYTES) throw new BridgeError('body_too_large', 413);
        const body = await readBody(request, length);
        send(response, 200, await bridge.request(operation, body));
      })().catch((error) => {
        const safe = error instanceof BridgeError ? error : new BridgeError('bridge_unavailable', 503);
        if (!response.headersSent) send(response, safe.status, { error: safe.code });
        else response.destroy();
      });
    });
    server.maxHeadersCount = 32;
    server.maxConnections = MAX_CONNECTIONS;
    server.maxRequestsPerSocket = 1;
    server.setTimeout(HTTP_TIMEOUT_MS);

    function closeServer() {
      closeTask ??= new Promise((resolve) => {
        accepting = false;
        listenAbort.abort();
        for (const socket of sockets) socket.destroy();
        sockets.clear();
        const deadline = setTimeout(resolve, 3000);
        deadline.unref();
        try {
          server.close(() => { clearTimeout(deadline); resolve(); });
        } catch { clearTimeout(deadline); resolve(); }
      });
      return closeTask;
    }
    server.on('connection', (socket) => {
      if (disposed || sockets.size >= MAX_CONNECTIONS) { socket.destroy(); return; }
      sockets.add(socket);
      socket.on('error', () => {});
      socket.once('close', () => sockets.delete(socket));
      socket.setTimeout(HTTP_TIMEOUT_MS, () => socket.destroy());
    });
    server.on('clientError', (_error, socket) => socket.destroy());
    for (const event of ['checkContinue', 'checkExpectation']) server.on(event, (_request, response) => {
      response.on('error', () => {});
      send(response, 400, { error: 'request_rejected' });
    });
    server.on('upgrade', (_request, socket) => socket.destroy());
    server.on('connect', (_request, socket) => socket.destroy());
    server.on('error', () => {
      accepting = false;
      warning(ctx, 'Alice bridge listener unavailable; connection was not established.');
      void closeServer();
      void bridge.dispose();
    });
    server.once('listening', () => {
      if (disposed) void closeServer();
      else accepting = true;
    });
    try {
      server.listen({ host: '127.0.0.1', port: selected.port, exclusive: true, signal: listenAbort.signal });
    } catch {
      warning(ctx, 'Alice bridge listener unavailable; connection was not established.');
      void closeServer();
      void bridge.dispose();
    }
    return {
      async stop() {
        disposed = true;
        accepting = false;
        await closeServer();
        lease.listenerStopped = !server.listening;
        await bridge.dispose();
        token.fill(0);
        if (lease.listenerStopped && bridge.cleanupConfirmed() && globalThis[LIFECYCLE_LEASE] === lease) {
          delete globalThis[LIFECYCLE_LEASE];
        }
      },
      cleanupConfirmed() {
        return !server.listening && bridge.cleanupConfirmed();
      },
    };
}
