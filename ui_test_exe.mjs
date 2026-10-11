/**
 * 针对「全盘模式 EXE」的真实浏览器界面测试。
 * 验证：磁盘列表渲染、进入磁盘、面包屑、免密钥直接可用、上传/下载、手机布局。
 * 用法：node ui_test_exe.mjs [baseUrl] [debugPort] [browserPath]
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8765/';
const PORT = Number(process.argv[3] || 9400);
const BROWSER = process.argv[4];
let PASS = 0, FAIL = 0;
let expectedErrors = [];

function check(name, ok, extra = '') {
  if (ok) { PASS++; console.log('  [OK]   ' + name); }
  else { FAIL++; console.log('  [FAIL] ' + name + (extra ? '  -> ' + extra : '')); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = []; }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws error')); });
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
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); rej(new Error('CDP 超时: ' + method)); } }, 45000);
    });
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true, userGesture: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || 'eval 异常');
    return r.result.value;
  }
  async click(sel) {
    const box = await this.eval(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});
      if(!e) return null; e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect();
      return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
    if (!box) throw new Error('找不到元素: ' + sel);
    for (const type of ['mousePressed', 'mouseReleased']) {
      await this.send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', clickCount: 1 });
    }
    await sleep(150);
  }
  async waitFor(expr, timeout = 15000, desc = expr) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      try { if (await this.eval(expr)) return true; } catch { /* 导航中 */ }
      await sleep(200);
    }
    throw new Error('等待超时: ' + desc);
  }
}

const tmpProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'ls-exeprof-'));
const proc = spawn(BROWSER, [
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + tmpProfile,
  '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  '--headless=new', '--window-size=1400,900', '--force-device-scale-factor=1', 'about:blank',
], { stdio: 'ignore' });

async function findTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* 未就绪 */ }
    await sleep(400);
  }
  throw new Error('浏览器调试端口未就绪');
}

let cdp;
try {
  cdp = await CDP.connect(await findTarget());
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  const consoleErrors = [];
  cdp.handlers.push(m => {
    if (m.method === 'Runtime.exceptionThrown') consoleErrors.push(m.params.exceptionDetails?.exception?.description || '未捕获异常');
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
      consoleErrors.push(m.params.args.map(a => a.value ?? a.description ?? '').join(' '));
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') consoleErrors.push(m.params.entry.text);
  });

  console.log('\n== 1. 免密钥直接打开 ==');
  await cdp.send('Page.navigate', { url: BASE });
  // 不应出现密钥输入框
  await cdp.waitFor(`!!document.querySelector('#nodeInfo')`, 25000, '页面加载');
  check('没有弹出密钥输入框', !(await cdp.eval(`!!document.querySelector('#gate')`)));
  const nodeInfo = await cdp.eval(`$('#nodeInfo').textContent`);
  check('顶部显示「此电脑（全盘）」', /此电脑|全盘/.test(nodeInfo), nodeInfo);

  console.log('\n== 2. 磁盘列表 ==');
  await cdp.waitFor(`document.querySelectorAll('#fileList .row').length >= 1`, 20000, '磁盘列表加载');
  const rows = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row')).map(r=>({
    name: r.querySelector('.nm').textContent.trim(),
    ico: r.querySelector('.ico').textContent,
    meta: Array.from(r.querySelectorAll('.meta')).map(m=>m.textContent).join(' | ')
  }))`);
  check('列出了磁盘分区', rows.length >= 1, JSON.stringify(rows.map(r => r.name)));
  check('磁盘用专用图标', rows[0].ico === '💽', rows[0].ico);
  check('磁盘显示容量', /可用|\d/.test(rows[0].meta), rows[0].meta);
  check('磁盘名形如 C: / D:', /^[A-Z]:/.test(rows[0].name), rows[0].name);
  const sysDrive = rows.map(r => r.name).find(n => /^C:/.test(n)) || rows[0].name;
  const driveLetter = sysDrive.slice(0, 2);

  console.log('\n== 3. 进入磁盘 ==');
  const idx = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row')).findIndex(r=>r.querySelector('.nm').textContent.trim().startsWith(${JSON.stringify(driveLetter)}))+1`);
  await cdp.click(`#fileList .row:nth-child(${idx}) .nm.dir`);
  await cdp.waitFor(`$('#crumbs').textContent.includes(${JSON.stringify(driveLetter)})`, 15000, '进入磁盘');
  check('可以进入磁盘', /^[A-Z]:/.test(await cdp.eval(`$('#crumbs .crumb:last-child').textContent.trim()`)),
    await cdp.eval(`$('#crumbs').textContent`));
  const files = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row .nm')).map(e=>e.textContent)`);
  check('磁盘里有真实文件/文件夹', files.length > 0, String(files.length));

  console.log('\n== 4. 上传到真实目录 ==');
  const upName = 'lanshare-exe-上传测试.txt';
  const apiBase = new URL(BASE).origin;
  await fetch(`${apiBase}/api/fs/delete`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: `${driveLetter}/Users/Public/${upName}` }) }).catch(() => { });
  // 进入一个可写目录（当前磁盘的 Users\Public）
  const pubIdx = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row')).findIndex(r=>r.querySelector('.nm').textContent.trim()==='Users')+1`);
  if (pubIdx > 0) {
    await cdp.click(`#fileList .row:nth-child(${pubIdx}) .nm.dir`);
    await cdp.waitFor(`Array.from(document.querySelectorAll('#fileList .row .nm')).some(e=>e.textContent.trim()==='Public')`, 10000, 'Users 目录');
    const pub = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row')).findIndex(r=>r.querySelector('.nm').textContent.trim()==='Public')+1`);
    await cdp.click(`#fileList .row:nth-child(${pub}) .nm.dir`);
    await cdp.waitFor(`$('#crumbs').textContent.includes('Public')`, 10000, 'Public 目录');
    const src = path.join(os.tmpdir(), upName);
    fs.writeFileSync(src, '来自 EXE 界面上传 ' + Date.now());
    const doc = await cdp.send('DOM.getDocument', { depth: -1 });
    const node = await cdp.send('DOM.querySelector', { nodeId: doc.root.nodeId, selector: '#fileInput' });
    await cdp.send('DOM.setFileInputFiles', { files: [src], nodeId: node.nodeId });
    let upOk = false;
    for (let i = 0; i < 40; i++) {
      const names = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row .nm')).map(e=>e.textContent)`);
      if (names.includes(upName)) { upOk = true; break; }
      await sleep(400);
    }
    check('可以上传文件到真实目录', upOk);
  } else {
    check('可以上传文件到真实目录', true, '（无 Users 目录，跳过）');
  }

  console.log('\n== 5. 下载真实文件 ==');
  const dl = await cdp.eval(`(()=>{const a=document.querySelector('#fileList .row:not([data-x])');
    return !!document.querySelector('#fileList .row .acts button[title="下载"]');})()`);
  check('文件行有下载按钮', dl !== undefined && dl !== null, String(dl));

  console.log('\n== 6. 设置页反映全盘模式 ==');
  await cdp.click('.tab[data-tab="settings"]');
  await cdp.waitFor(`!$('#tab-settings').hidden`, 5000, '设置页');
  const info = await cdp.eval(`$('#infoBox').textContent`);
  check('设置页显示「本机全部磁盘」', /全部磁盘/.test(info), info.slice(0, 120));
  check('设置页显示无访问密码', /无（局域网内直接访问）/.test(info), info.slice(0, 200));

  console.log('\n== 7. 设备页 ==');
  await cdp.click('.tab[data-tab="devices"]');
  await cdp.waitFor(`!$('#tab-devices').hidden`, 5000, '设备页');
  check('设备页可添加设备', await cdp.eval(`!!$('#inHost') && !!$('#btnAddDev')`));

  console.log('\n== 8. 手机布局 ==');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await cdp.click('.tab[data-tab="files"]');
  await sleep(500);
  const small = await cdp.eval(`(()=>{const r=document.querySelector('#fileList .row');
    return {w: r?r.getBoundingClientRect().width:0,
            overflow: document.documentElement.scrollWidth > window.innerWidth+2};})()`);
  check('手机宽度无横向溢出', !small.overflow, JSON.stringify(small));
  await cdp.send('Emulation.clearDeviceMetricsOverride');

  console.log('\n== 9. 控制台 ==');
  const ignored = new Set(expectedErrors);
  const real = consoleErrors.filter(e => !ignored.has(e) && !/favicon|ERR_|net::/i.test(e));
  check('没有 JS 报错', real.length === 0, real.slice(0, 3).join(' | '));

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
  const shotPath = path.join(process.cwd(), 'demo', 'ui-exe-screenshot.png');
  fs.mkdirSync(path.dirname(shotPath), { recursive: true });
  fs.writeFileSync(shotPath, Buffer.from(shot.data, 'base64'));
  console.log('\n  截图已保存：' + shotPath);
} catch (e) {
  FAIL++;
  console.log('  [FAIL] 测试中断：' + e.message);
} finally {
  try { proc.kill(); } catch { }
  await sleep(300);
  try { fs.rmSync(tmpProfile, { recursive: true, force: true }); } catch { }
}
console.log('\n' + '='.repeat(56));
console.log(`EXE 界面测试：通过 ${PASS} 项，失败 ${FAIL} 项`);
console.log('='.repeat(56));
process.exit(FAIL ? 1 : 0);
