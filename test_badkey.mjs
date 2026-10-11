/**
 * 访问密码中的非 ASCII 字符回归测试。
 *
 * 背景（真实故障）：用户把访问密码设成中文后，浏览器在 fetch() 构造阶段就抛
 *   “Failed to read the 'headers' property from RequestInit:
 *    String contains non ISO-8859-1 code point”
 * 整个界面显示「网络不可达」，完全不可用 —— 连清掉密码都做不到。
 *
 * 本测试覆盖：
 *   1. 设了中文密码后，用正确密码能正常访问（头部经 base64 编码）
 *   2. 密码错误时给出可读提示，而不是「网络不可达」
 *   3. localStorage 里残留的旧坏密码不会把界面卡死（自动清除）
 *   4. 页面上依然能勾选/浏览，功能未受影响
 *
 * 用法：node test_badkey.mjs <baseUrl> <debugPort> <browserPath> <serverPort>
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8765/';
const DPORT = Number(process.argv[3] || 9500);
const BROWSER = process.argv[4];
const SPORT = Number(process.argv[5] || 8765);
const CN_KEY = '中文密码测试🔒';

const sleep = ms => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
const check = (n, ok, ex = '') => {
  if (ok) { pass++; console.log('  [OK]   ' + n); }
  else { fail++; console.log('  [FAIL] ' + n + (ex ? '  -> ' + ex : '')); }
};

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = []; }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws')); });
    const c = new CDP(ws);
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.id && c.pending.has(m.id)) {
        const { res, rej } = c.pending.get(m.id); c.pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method) c.handlers.forEach(h => h(m));
    };
    return c;
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); rej(new Error('timeout ' + method)); } }, 30000);
    });
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || 'eval err');
    return r.result.value;
  }
  async waitFor(expr, timeout = 20000, desc = '') {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      try { if (await this.eval(expr)) return true; } catch { }
      await sleep(200);
    }
    throw new Error('等待超时: ' + (desc || expr));
  }
}

const api = async (p, method = 'GET', body, key) => {
  const headers = {};
  if (key) headers['X-LanShare-Key'] = /^[\x20-\x7E]*$/.test(key) ? key : 'b64:' + Buffer.from(key, 'utf8').toString('base64');
  if (body) headers['Content-Type'] = 'application/json';
  const r = await fetch(`http://127.0.0.1:${SPORT}${p}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
};

const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'ls-badkey-'));
const proc = spawn(BROWSER, ['--remote-debugging-port=' + DPORT, '--user-data-dir=' + prof,
  '--no-first-run', '--no-default-browser-check', '--headless=new',
  '--window-size=1200,800', 'about:blank'], { stdio: 'ignore' });

let cdp;
try {
  let wsUrl = null;
  for (let i = 0; i < 60 && !wsUrl; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${DPORT}/json/list`)).json();
      wsUrl = (list.find(t => t.type === 'page' && t.webSocketDebuggerUrl) || {}).webSocketDebuggerUrl;
    } catch { }
    if (!wsUrl) await sleep(400);
  }
  cdp = await CDP.connect(wsUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  // ---- 第 0 步：先确保服务端是免密的干净状态 ----
  let r = await api('/api/config', 'POST', { key: '' });
  check('重置为免密状态', r.status === 200, JSON.stringify(r.json));

  // ---- 第 1 步：设置成中文密码 ----
  r = await api('/api/config', 'POST', { key: CN_KEY });
  check('可以设置含中文/emoji 的密码', r.status === 200 && r.json.config.key === CN_KEY,
    JSON.stringify(r.json && r.json.config && r.json.config.key));
  r = await api('/api/fs/list?path=C:/Windows', 'GET', null, CN_KEY);
  check('用中文密码能正常访问（头部 base64 编码生效）', r.status === 200 && r.json.ok, r.status);
  r = await api('/api/fs/list?path=C:/Windows', 'GET', null, '错误密码');
  check('密码错误时返回 401（不是崩溃）', r.status === 401, r.status);

  // ---- 第 2 步：浏览器里用中文密码打开界面 ----
  console.log('\n== 浏览器中用中文密码 ==');
  await cdp.send('Page.navigate', { url: BASE });
  await cdp.waitFor(`!!document.querySelector('#nodeInfo')`, 25000, '页面载入');
  await cdp.eval(`localStorage.setItem('ls_key', ${JSON.stringify(CN_KEY)}); 'ok'`);
  await cdp.send('Page.navigate', { url: BASE + (BASE.includes('?') ? '&' : '?') + 'r=' + Date.now() });
  await sleep(4500);
  let top = await cdp.eval(`document.querySelector('#nodeInfo').textContent`);
  check('中文密码下不再出现「网络不可达」', !/网络不可达/.test(top), top);
  check('中文密码下不出现 ISO-8859-1 报错',
    !/ISO-8859-1/i.test(await cdp.eval(`document.body.innerText`)));
  let rows = await cdp.eval(`document.querySelectorAll('#fileList .row').length`);
  check('中文密码下文件列表正常加载', rows > 0, '条目数=' + rows);

  // ---- 第 3 步：localStorage 里残留的旧坏密码不再卡死界面 ----
  console.log('\n== 存量坏密码的容错 ==');
  // 服务端改回免密（必须带上当前密码才改得动），浏览器里则留着旧的坏值
  r = await api('/api/config', 'POST', { key: '' }, CN_KEY);
  check('带密码可以改回免密', r.status === 200, JSON.stringify(r.json));
  await cdp.eval(`localStorage.setItem('ls_key','坏的旧密码\u0001中文'); 'ok'`);
  await cdp.send('Page.navigate', { url: BASE + (BASE.includes('?') ? '&' : '?') + 'r=' + Date.now() });
  await sleep(4500);
  top = await cdp.eval(`document.querySelector('#nodeInfo').textContent`);
  const body = await cdp.eval(`document.body.innerText`);
  check('带控制字符的坏密码也不会让界面「网络不可达」',
    !/网络不可达/.test(top) && !/ISO-8859-1/i.test(body), top);
  rows = await cdp.eval(`document.querySelectorAll('#fileList .row').length`);
  check('坏密码存在时仍能正常浏览（服务端已免密）', rows > 0, '条目数=' + rows);

  // ---- 第 4 步：免密状态下功能完全正常 ----
  console.log('\n== 恢复正常状态 ==');
  await cdp.eval(`localStorage.removeItem('ls_key'); 'ok'`);
  await cdp.send('Page.navigate', { url: BASE + (BASE.includes('?') ? '&' : '?') + 'r=' + Date.now() });
  // 等文件列表真正加载出来，而不是死等固定时长
  let loaded = false;
  for (let i = 0; i < 40; i++) {
    const n = await cdp.eval(`document.querySelectorAll('#fileList .row').length`).catch(() => 0);
    if (n > 0) { loaded = true; break; }
    await sleep(400);
  }
  top = await cdp.eval(`document.querySelector('#nodeInfo').textContent`);
  rows = await cdp.eval(`document.querySelectorAll('#fileList .row').length`);
  check('清除后免密正常访问', loaded && !/失败|不可达/.test(top), top + ' | 条目=' + rows);
} catch (e) {
  fail++;
  console.log('  [FAIL] 中断：' + e.message);
} finally {
  try { await api('/api/config', 'POST', { key: '' }); } catch { }
  try { proc.kill(); } catch { }
  await sleep(300);
  try { fs.rmSync(prof, { recursive: true, force: true }); } catch { }
}
console.log('\n' + '='.repeat(56));
console.log(`非 ASCII 密码测试：通过 ${pass} 项，失败 ${fail} 项`);
console.log('='.repeat(56));
process.exit(fail ? 1 : 0);
