export class AliceClient {
  constructor(config, token) { this.base = config.base_url; this.token = token; }
  async call(method, suffix, body) {
    const response = await fetch(`${this.base}/api/channels/v1/${suffix}`, {
      method, redirect: 'error', signal: AbortSignal.timeout(10000),
      headers: { Authorization: `Bearer ${this.token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const reader = response.body?.getReader();
    if (!reader) throw new Error('empty_response');
    const chunks = []; let size = 0;
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      size += value.length;
      if (size > 256 * 1024) { await reader.cancel(); throw new Error('oversize_response'); }
      chunks.push(Buffer.from(value));
    }
    const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid_response');
    return { code: response.status, data };
  }
  status() { return this.call('GET', 'status'); }
  submit(body) { return this.call('POST', 'requests', body); }
  result(id) { return this.call('GET', `requests/${encodeURIComponent(id)}`); }
  delivery(body) { return this.call('POST', 'deliveries', body); }
}
