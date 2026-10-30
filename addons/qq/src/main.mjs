import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
import readline from 'node:readline/promises';
import { QQBot } from '@tencent-connect/qqbot-nodejs';
import { loadConfig, readSecret } from './config.mjs';
import { Ledger, SafeLog, acquireLock, unlockStale, atomicJSON, hash, privateDirectory } from './storage.mjs';
import { AliceClient } from './alice-client.mjs';
import { Bridge } from './bridge.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const args = process.argv.slice(2);
const command = args[0] ?? 'help';
const ix = args.indexOf('--config');
const file = path.resolve(ix >= 0 ? args[ix + 1] ?? '' : path.join(root, 'user/qq/config.json'));
if (args.some((a, i) => i > 0 && a !== '--config' && i !== ix + 1)) throw new Error('参数无效');
const controller = new AbortController();
process.on('SIGINT', () => controller.abort());
process.on('SIGTERM', () => controller.abort());

async function confirm(prompt) {
  if (!process.stdin.isTTY || !process.stdout.isTTY) throw new Error('此命令需要前台交互终端');
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try { return (await rl.question(prompt, { signal: controller.signal })).trim(); }
  finally { rl.close(); }
}
function configRaw() { return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')); }
function coreSnippet(c) {
  const token = readSecret(c.alice.machine_token_file);
  console.log('\n把以下非秘密配置合并到 user/config.toml（不要覆盖原 [llm]）：');
  console.log(`[channels]\nenabled = true\nprotocol_version = 1\n\n[channels.qq_owner]\nenabled = true\ntoken_sha256 = "${hash(token)}"\nprovider = "qq-official"\napp_id = "${c.qq.app_id}"\nsender_id = "${c.qq.owner_openid}"\nchat_type = "c2c"`);
  console.log('配置生效需要用户受控重启 Alice。此命令没有启动桥或 Alice。');
}
function makeBot(c, log = null) {
  return new QQBot({ appId: c.qq.app_id, appSecret: readSecret(c.qq.app_secret_file),
    accountId: 'alice-qq-owner', baseUrl: 'https://api.bot.qq.com', transport: 'websocket',
    tokenPrefetch: 'sync', intents: 1 << 25, markdownSupport: false,
    logger: { info: () => {}, warn: () => {}, error: () => log?.write('qq_sdk_error') },
  });
}
function createToken(c) {
  if (fs.existsSync(c.alice.machine_token_file)) return;
  privateDirectory(path.dirname(c.alice.machine_token_file));
  const fd = fs.openSync(c.alice.machine_token_file, 'wx', 0o600);
  try { fs.writeFileSync(fd, randomBytes(32).toString('base64url')); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
}
async function scan(c) {
  if (!process.stdin.isTTY || !process.stdout.isTTY) throw new Error('scan 必须在前台终端运行，二维码不可写入后台日志');
  if (fs.existsSync(c.qq.app_secret_file)) throw new Error('目标已有 AppSecret；未覆盖。请使用新的配置目录进行重新授权');
  const { startQrConnect } = await import('@tencent-connect/qqbot-connector');
  const { default: qr } = await import('qrcode-terminal');
  let dispose = () => {};
  const timer = setTimeout(() => controller.abort(), 300000);
  try {
    console.log('使用手机 QQ 扫码授权官方机器人；接入方显示“第三方机器人”。5 分钟后自动取消。');
    const credentials = await new Promise((resolve, reject) => {
      const cancelled = () => { dispose(); reject(new Error('扫码取消或超时')); };
      controller.signal.addEventListener('abort', cancelled, { once: true });
      dispose = startQrConnect({
        onQrDisplayed(url) {
          if (controller.signal.aborted) return;
          const u = new URL(url);
          if (u.protocol !== 'https:' || u.hostname !== 'q.qq.com' || u.username || u.password) { controller.abort(); return; }
          qr.generate(url, { small: true }); // Foreground only; never write the URL or QR to logs.
        },
        onQrExpired() { console.log('二维码已过期，等待官方刷新；总时限不延长。'); },
        onSuccess(value) { controller.signal.removeEventListener('abort', cancelled); resolve(value); },
        onFailure() { controller.signal.removeEventListener('abort', cancelled); reject(new Error('扫码未完成，请核对官方账号权限后重试')); },
      }, { displayQrCodeToConsole: false, source: '', signal: controller.signal });
    });
    dispose();
    if (!Array.isArray(credentials) || credentials.length !== 1) throw new Error('只支持一个机器人，未写入凭据');
    const value = credentials[0];
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(value.appId) || !/^[\x21-\x7e]{16,4096}$/.test(value.appSecret)) throw new Error('返回凭据格式无效');
    console.log(`官方机器人 AppID：${value.appId}`);
    let owner = '';
    if (/^[A-Za-z0-9_-]{1,128}$/.test(value.userOpenid ?? '')) {
      console.log(`扫码身份候选 OpenID：${value.userOpenid}`);
      if (await confirm('确认这是你本人，且只允许此身份私聊？输入 YES 保存，其他输入保留待配对：') === 'YES') owner = value.userOpenid;
    }
    privateDirectory(path.dirname(c.qq.app_secret_file));
    const fd = fs.openSync(c.qq.app_secret_file, 'wx', 0o600);
    try { fs.writeFileSync(fd, value.appSecret); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
    const raw = configRaw(); raw.qq.app_id = value.appId; raw.qq.owner_openid = owner; atomicJSON(file, raw);
    console.log('机器人凭据已保存；没有启动聊天，也没有修改 Alice 模型配置。');
    if (owner) coreSnippet(loadConfig(file));
    else console.log('下一步运行 pair，本机确认所有者；随后运行 config 显示核心配置片段。');
  } finally { clearTimeout(timer); dispose(); }
}
async function pair(c) {
  if (!process.stdin.isTTY || !process.stdout.isTTY) throw new Error('pair 需要前台终端');
  const code = randomBytes(10).toString('hex');
  const bot = makeBot(c);
  const timer = setTimeout(() => controller.abort(), 300000);
  try {
    console.log(`在你本人的 QQ 私聊机器人发送：/pair ${code}`);
    console.log('配对五分钟内有效；不会把消息提交 Alice，也不会向 QQ 发送任何回复。');
    const candidate = await new Promise((resolve, reject) => {
      bot.on('message', (_ctx, msg) => {
        if (msg.kind !== 'c2c' || msg.rawEventType !== 'C2C_MESSAGE_CREATE' || msg.content?.trim() !== `/pair ${code}` ||
            msg.raw?.author?.user_openid !== msg.senderId || msg.raw?.id !== msg.messageId ||
            !/^[A-Za-z0-9_-]{1,128}$/.test(msg.senderId)) return;
        resolve(msg.senderId); bot.stop();
      });
      controller.signal.addEventListener('abort', () => { bot.stop(); reject(new Error('配对取消或超时')); }, { once: true });
      bot.start(controller.signal).catch(() => reject(new Error('QQ连接未成功，请核对机器人权限与凭据')));
    });
    console.log(`待确认所有者 OpenID：${candidate}`);
    if (await confirm('确认来自你本人？输入 YES 保存：') !== 'YES') throw new Error('未确认，绑定保持不变');
    const raw = configRaw(); raw.qq.owner_openid = candidate; atomicJSON(file, raw);
    coreSnippet(loadConfig(file));
  } finally { clearTimeout(timer); bot.stop(); }
}
async function run(c) {
  const token = readSecret(c.alice.machine_token_file);
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(token)) throw new Error('机器凭据须为至少32随机字节的base64url token');
  const ledger = new Ledger(c.storage.state_dir);
  const log = new SafeLog(c.storage.log_dir);
  const client = new AliceClient(c.alice, token);
  const first = await client.status();
  if (first.code !== 200 || first.data.protocol_version !== 1 || !['ready', 'busy'].includes(first.data.status)) {
    throw new Error('Alice 未就绪或机器凭据未启用；未连接 QQ');
  }
  const bot = makeBot(c, log);
  const bridge = new Bridge(c, ledger, log, client, bot);
  bridge.health = first.data;
  bot.on('message', (_ctx, msg) => { try { bridge.receive(msg); } catch { bridge.stopRequested = true; bot.stop(); } });
  bot.on('ready', () => log.write('qq_connected'));
  bot.on('resumed', () => log.write('qq_resumed'));
  bot.on('error', () => log.write('qq_connection_error'));
  const stopFile = path.join(c.storage.state_dir, 'stop.request');
  if (fs.existsSync(stopFile)) fs.unlinkSync(stopFile);
  const timer = setInterval(() => {
    if (controller.signal.aborted || fs.existsSync(stopFile)) { bridge.stopRequested = true; bot.stop(); }
  }, 500);
  const gateway = bot.start(controller.signal).catch(() => { log.write('qq_start_failed'); bridge.stopRequested = true; });
  try { await bridge.run(); }
  finally {
    clearInterval(timer); bot.stop();
    // SDK 1.0.4 can miss abort while its initial connection is awaiting token/gateway.
    // Do not leave the local lock held on a promise that has no remaining work.
    let timeout;
    await Promise.race([gateway, new Promise(resolve => { timeout = setTimeout(resolve, 15000); })]);
    clearTimeout(timeout);
    log.write('bridge_stopped');
  }
}
async function main() {
  if (command === 'help') {
    console.log('Alice QQ 桥：node src/main.mjs <init|scan|pair|config|run|stop|unlock> [--config 绝对路径]\n默认 user/qq/config.json；init/scan/pair仅在用户明确执行时写本机配置。'); return;
  }
  if (command === 'init') {
    if (fs.existsSync(file)) throw new Error('配置已存在，未覆盖');
    privateDirectory(path.dirname(file));
    fs.copyFileSync(path.join(root, 'addons/qq/config.example.json'), file, fs.constants.COPYFILE_EXCL);
    createToken(loadConfig(file, { pairing: true, onboarding: true }));
    console.log('已创建桥配置和独立机器凭据，尚未启用接入。下一步运行 scan，或手工填AppID/AppSecret后运行pair。'); return;
  }
  const c = loadConfig(file, { pairing: command !== 'run' && command !== 'config', onboarding: command === 'scan' });
  if (command === 'stop') {
    if (!fs.existsSync(path.join(c.storage.state_dir, 'bridge.lock'))) throw new Error('未找到桥实例锁');
    fs.writeFileSync(path.join(c.storage.state_dir, 'stop.request'), 'stop', { mode: 0o600 });
    console.log('已请求桥停止；等待 bridge_stopped 或锁文件消失。没有关闭 Alice。'); return;
  }
  if (command === 'unlock') { unlockStale(c.storage.state_dir); console.log('已移除确认失效的实例锁。'); return; }
  if (command === 'config') { coreSnippet(c); return; }
  if (!['scan', 'pair', 'run'].includes(command)) throw new Error('未知命令');
  const release = acquireLock(c.storage.state_dir);
  try {
    if (command === 'scan') { createToken(c); await scan(c); }
    else if (command === 'pair') await pair(c);
    else await run(c);
  } finally { release(); }
}
main().catch(error => {
  // Keep platform/provider exception messages and credentials out of console/logs.
  const safe = /^(此命令|目标已有|只支持一个|返回凭据|扫码|pair |配对|QQ连接|未确认|机器凭据|Alice 未|配置已|未找到|未知命令|桥已有|该 PID|锁损坏|桥账本)/.test(error.message);
  console.error(safe ? error.message : '操作未完成；请按 README 核对配置、文件权限、依赖和网络。未输出原始异常。');
  process.exitCode = 1;
});
