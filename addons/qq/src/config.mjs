import fs from 'node:fs';
import path from 'node:path';

export function object(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some(k => !keys.includes(k))) throw new Error(`${label}: 配置字段无效`);
  return value;
}
function string(value, label, empty = false) {
  if (typeof value !== 'string' || (!empty && !value.trim()) || value.length > 2048 || /[\r\n\0]/.test(value)) {
    throw new Error(`${label}: 请填写有效文本`);
  }
  return value.trim();
}
function integer(value, min, max, label) {
  if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${label}: 数值超出支持范围`);
  return value;
}
export function loadConfig(file, { pairing = false, onboarding = false } = {}) {
  const configPath = path.resolve(file);
  const stat = fs.statSync(configPath);
  if (!stat.isFile() || stat.size > 16384) throw new Error('配置文件过大或类型无效');
  const c = JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^\uFEFF/, ''));
  object(c, ['qq', 'alice', 'delivery', 'storage'], '配置');
  object(c.qq, ['transport', 'app_id', 'app_secret_file', 'owner_openid', 'allow_groups', 'allow_proactive'], 'qq');
  object(c.alice, ['base_url', 'channel_id', 'machine_token_file'], 'alice');
  object(c.delivery, ['queue_limit', 'queue_ttl_seconds', 'passive_window_seconds', 'max_reply_segments', 'send_spacing_ms', 'waiting_notice'], 'delivery');
  object(c.storage, ['state_dir', 'log_dir'], 'storage');
  if (c.qq.transport !== 'websocket' || c.qq.allow_groups !== false || c.qq.allow_proactive !== false || c.delivery.waiting_notice !== false) {
    throw new Error('首期仅支持 WebSocket、本人私聊、被动文本；输入提示须关闭');
  }
  c.qq.app_id = string(c.qq.app_id, 'app_id');
  c.qq.owner_openid = string(c.qq.owner_openid, 'owner_openid', pairing);
  if ((!onboarding && !/^[A-Za-z0-9_-]{1,128}$/.test(c.qq.app_id)) || (!pairing && !/^[A-Za-z0-9_-]{1,128}$/.test(c.qq.owner_openid))) {
    throw new Error('请填写真实 AppID 和已确认的 owner_openid');
  }
  const url = new URL(string(c.alice.base_url, 'base_url'));
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Alice 地址必须是 http://127.0.0.1:端口，不可带路径或凭据');
  }
  c.alice.base_url = url.origin;
  if (c.alice.channel_id !== 'qq-owner') throw new Error('channel_id 必须为 qq-owner');
  integer(c.delivery.queue_limit, 1, 3, 'queue_limit');
  integer(c.delivery.queue_ttl_seconds, 10, 120, 'queue_ttl_seconds');
  integer(c.delivery.passive_window_seconds, 60, 300, 'passive_window_seconds');
  integer(c.delivery.max_reply_segments, 1, 3, 'max_reply_segments');
  integer(c.delivery.send_spacing_ms, 1000, 60000, 'send_spacing_ms');
  for (const [obj, key] of [[c.qq, 'app_secret_file'], [c.alice, 'machine_token_file'], [c.storage, 'state_dir'], [c.storage, 'log_dir']]) {
    obj[key] = path.resolve(path.dirname(configPath), string(obj[key], key));
  }
  c.configPath = configPath;
  return c;
}
export function readSecret(file) {
  const stat = fs.statSync(file);
  if (!stat.isFile() || stat.size < 16 || stat.size > 4096) throw new Error('凭据文件长度无效');
  const secret = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').trim();
  if (!/^[\x21-\x7e]{16,4096}$/.test(secret)) throw new Error('凭据格式无效');
  return secret;
}
