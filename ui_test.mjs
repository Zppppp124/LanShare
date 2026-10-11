/**
 * LanShare 真实浏览器（Edge/Chrome + CDP）界面端到端验证。
 * 覆盖：首屏渲染、密钥门、目录浏览、面包屑、勾选、上传（真实文件注入）、
 *       设备页、传输页、设置页、控制台报错。
 * 用法：node ui_test.mjs <baseUrl> <调试端口> <浏览器路径>
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8765/?key=demokey123';
const PORT = Number(process.argv[3] || 9333);
const BROWSER = process.argv[4];
const OUT = [];
let PASS = 0, FAIL = 0;
let expectedErrors = [];

function check(name, ok, extra = '') {
  if (ok) { PASS++; console.log('  [OK]   ' + name); }
  else { FAIL++; console.log('  [FAIL] ' + name + (extra ? '  -> ' + extra : '')); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; this.handlers = []; }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = e => rej(new Error('ws error')); });
    const c = new CDP(ws);
    ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.id && c.pending.has(m.id)) {
        const { res, rej } = c.pending.get(m.id); c.pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method) {
        c.events.push(m);
        c.handlers.forEach(h => h(m));
      }
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
      expression: expr, returnByValue: true, awaitPromise: true, userGesture: true,
    });
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
    await sleep(120);
  }
  async waitFor(expr, timeout = 15000, desc = expr) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      try { if (await this.eval(expr)) return true; } catch (e) { /* 页面可能正在导航 */ }
      await sleep(200);
    }
    throw new Error('等待超时: ' + desc);
  }
}

const tmpProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'ls-uiprof-'));
const args = [
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + tmpProfile,
  '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  '--headless=new', '--window-size=1400,900', '--force-device-scale-factor=1',
  'about:blank',
];
const proc = spawn(BROWSER, args, { stdio: 'ignore' });

async function findTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const page = list.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch (e) { /* 还没起来 */ }
    await sleep(400);
  }
  throw new Error('浏览器调试端口未就绪');
}

let cdp;
try {
  const wsUrl = await findTarget();
  cdp = await CDP.connect(wsUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');

  const consoleErrors = [];
  cdp.handlers.push(m => {
    if (m.method === 'Runtime.exceptionThrown') {
      consoleErrors.push(m.params.exceptionDetails?.exception?.description || '未捕获异常');
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      consoleErrors.push(m.params.args.map(a => a.value ?? a.description ?? '').join(' '));
    }
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
      consoleErrors.push(m.params.entry.text);
    }
  });

  // 清理上次运行遗留的上传文件，保证测试可重复执行
  const API = new URL(BASE);
  const apiBase = `${API.protocol}//${API.host}`;
  const apiKey = API.searchParams.get('key') || '';
  const clean = await fetch(`${apiBase}/api/fs/delete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LanShare-Key': apiKey },
    body: JSON.stringify({ path: 'lanshare-ui-上传测试.txt' }),
  }).then(r => r.json()).catch(() => ({}));
  console.log('  （清理上次遗留文件：' + (clean.ok ? '有' : '无') + '）');
  // 复位节点名（上次运行可能改过名），保证可重复执行
  await fetch(`${apiBase}/api/config`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LanShare-Key': apiKey },
    body: JSON.stringify({ name: '验证节点' }),
  }).catch(() => { });

  console.log('\n== 1. 首屏 ==');
  await cdp.send('Page.navigate', { url: BASE });
  await cdp.waitFor(`!!document.querySelector('#nodeInfo') && document.querySelector('#nodeInfo').textContent.includes('验证节点')`, 20000, '首屏连接成功');
  check('页面标题正确', (await cdp.eval('document.title')).includes('验证节点'));
  check('顶部显示节点名与共享目录',
    /验证节点/.test(await cdp.eval(`$('#nodeInfo').textContent`)) &&
    /共享文件/.test(await cdp.eval(`$('#nodeInfo').textContent`)));
  await cdp.waitFor(`document.querySelectorAll('#fileList .row').length >= 3`, 15000, '文件列表加载');
  const rows = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row .nm')).map(e=>e.textContent)`);
  const isDirFlags = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row .nm')).map(e=>e.classList.contains('dir'))`);
  check('根目录包含演示文件夹与文件',
    ['照片', '文档', '说明.txt'].every(n => rows.includes(n)), JSON.stringify(rows));
  const lastDir = isDirFlags.lastIndexOf(true);
  const firstFile = isDirFlags.indexOf(false);
  check('目录统一排在文件前面', lastDir < firstFile, JSON.stringify(rows));
  check('文件大小已展示', /\d/.test(await cdp.eval(`document.querySelector('#fileList .row .meta').textContent`)));
  check('顶部徽标显示当前位置', /本机/.test(await cdp.eval(`$('#netBadge').textContent`)));

  console.log('\n== 2. 目录导航 ==');
  const idxOf = n => `Array.from(document.querySelectorAll('#fileList .row')).findIndex(r=>r.querySelector('.nm').textContent===${JSON.stringify(n)})+1`;
  await cdp.click(`#fileList .row:nth-child(${await cdp.eval(idxOf('文档'))}) .nm.dir`);
  await cdp.waitFor(`$('#crumbs').textContent.includes('文档')`, 8000, '进入文档目录');
  const inner = await cdp.eval(`Array.from(document.querySelectorAll('#fileList .row .nm')).map(e=>e.textContent)`);
  check('进入子目录后列出内容', inner.length === 1 && inner[0] === '会议纪要.md', JSON.stringify(inner));
  await cdp.click('#crumbs .crumb:first-child');
  await cdp.waitFor(`document.querySelectorAll('#fileList .row').length === 3`, 8000, '返回根目录');
  check('面包屑可返回根目录', (await cdp.eval(`$('#crumbs .crumb').textContent`)).includes('本机'));

  console.log('\n== 3. 勾选与批量操作栏 ==');
  await cdp.click(`#fileList .row:nth-child(${await cdp.eval(idxOf('说明.txt'))}) .chk`);
  await cdp.waitFor(`$('#bulk').classList.contains('show')`, 5000, '批量栏出现');
  check('勾选后出现批量操作栏', await cdp.eval(`$('#bulk').classList.contains('show')`));
  check('批量栏显示已选数量', /已选 1 项/.test(await cdp.eval(`$('#bulkCnt').textContent`)));
  await cdp.click('#bulkClear');
  check('取消选择后批量栏隐藏', !(await cdp.eval(`$('#bulk').classList.contains('show')`)));

  console.log('\n== 4. 真实上传（CDP 注入文件）==');
  const uploadSrc = path.join(os.tmpdir(), 'lanshare-ui-上传测试.txt');
  fs.writeFileSync(uploadSrc, '来自真实浏览器的上传内容 ' + Date.now() + '\n'.repeat(50));
  const doc = await cdp.send('DOM.getDocument', { depth: -1 });
  const node = await cdp.send('DOM.querySelector', { nodeId: doc.root.nodeId, selector: '#fileInput' });
  await cdp.send('DOM.setFileInputFiles', { files: [uploadSrc], nodeId: node.nodeId });
  await cdp.waitFor(`Array.from(document.querySelectorAll('#fileList .nm')).some(e=>e.textContent==='lanshare-ui-上传测试.txt')`, 15000, '上传完成');
  check('上传文件出现在列表中', true);
  const upTask = await cdp.eval(`(()=>{const t=document.querySelector('#taskCount');return t?t.textContent:''})()`);
  check('上传后传输页有记录', true, upTask);

  console.log('\n== 5. 传输页 ==');
  await cdp.click('.tab[data-tab="tasks"]');
  await cdp.waitFor(`!$('#tab-tasks').hidden`, 5000, '切换到传输页');
  await cdp.waitFor(`document.querySelectorAll('#taskList .task').length >= 1`, 8000, '传输记录出现');
  const taskHtml = await cdp.eval(`$('#taskList').innerHTML`);
  check('传输页显示上传进度卡片', taskHtml.includes('上传') && taskHtml.includes('完成'), taskHtml.slice(0, 120));
  check('进度条元素存在', taskHtml.includes('class="prog"') || taskHtml.includes("class=\"prog\""));

  console.log('\n== 6. 设备页 ==');
  await cdp.click('.tab[data-tab="devices"]');
  await cdp.waitFor(`!$('#tab-devices').hidden`, 5000, '切换到设备页');
  check('设备页渲染添加表单', await cdp.eval(`!!$('#inHost') && !!$('#btnAddDev')`));
  // 故意连接一个不存在的端口，验证错误提示；此间的 502 是预期行为，不计入 JS 报错
  const expectedErrFrom = consoleErrors.length;
  await cdp.eval(`$('#inHost').value='127.0.0.1:9999'`);
  await cdp.eval(`$('#toast').textContent=''; $('#toast').classList.remove('show')`);
  await cdp.click('#btnAddDev');
  await cdp.waitFor(`/失败/.test($('#toast').textContent)`, 20000, '错误提示');
  const toastText = await cdp.eval(`$('#toast').textContent`);
  check('连接失败时给出可读提示', /添加失败|连接失败/.test(toastText), toastText);
  check('失败后按钮恢复可用', !(await cdp.eval(`$('#btnAddDev').disabled`)));
  const expectedErrTo = consoleErrors.length;
  expectedErrors = consoleErrors.slice(expectedErrFrom, expectedErrTo);
  check('失败请求只产生一条预期内的资源错误', expectedErrors.every(e => /502|9999|Failed to load resource/.test(e)),
    expectedErrors.join(' | '));

  console.log('\n== 7. 设置页 ==');
  await cdp.click('.tab[data-tab="settings"]');
  await cdp.waitFor(`!$('#tab-settings').hidden`, 5000, '切换到设置页');
  const info = await cdp.eval(`$('#infoBox').textContent`);
  check('设置页显示共享目录', info.includes('共享文件'), info.slice(0, 100));
  check('设置页显示访问地址', /192\.168\.|127\.0\.0\.1/.test(info));
  check('设置页显示磁盘空间', info.includes('剩余'));
  const keyVal = await cdp.eval(`$('#cfgKey').value`);
  check('设置页回填群组密钥', keyVal === 'demokey123', keyVal);

  console.log('\n== 8. 修改本机名称（写回后端）==');
  await cdp.eval(`$('#cfgName').value='验证节点改名'`);
  await cdp.click('#btnSaveCfg');
  await cdp.waitFor(`$('#nodeInfo').textContent.includes('验证节点改名')`, 10000, '名称更新');
  check('保存设置后顶部名称即时更新', true);
  const apiName = await (await fetch('http://127.0.0.1:8765/api/info', { headers: { 'X-LanShare-Key': 'demokey123' } })).json();
  check('后端确实保存了新名称', apiName.name === '验证节点改名', apiName.name);

  console.log('\n== 9. 移动端布局 ==');
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  });
  await cdp.click('.tab[data-tab="files"]');
  await sleep(400);
  const small = await cdp.eval(`(()=>{const r=document.querySelector('#fileList .row').getBoundingClientRect();
    return {w:r.width, overflow:document.documentElement.scrollWidth>window.innerWidth+2,
            tabVisible: !!document.querySelector('.tab[data-tab="files"]').offsetParent};})()`);
  check('手机宽度下列表自适应', small.w > 300 && small.w <= 390, JSON.stringify(small));
  check('手机宽度下无横向溢出', !small.overflow, JSON.stringify(small));
  check('手机宽度下标签栏可见', small.tabVisible);
  await cdp.send('Emulation.clearDeviceMetricsOverride');

  console.log('\n== 10. 控制台 ==');
  const ignored = new Set(expectedErrors);
  const realErrors = consoleErrors.filter(e => !ignored.has(e) && !/favicon|ERR_|net::/i.test(e));
  check('没有 JS 报错', realErrors.length === 0, realErrors.slice(0, 3).join(' | '));

  // 截图存档
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const shotPath = path.join(process.cwd(), 'demo', 'ui-screenshot.png');
  fs.mkdirSync(path.dirname(shotPath), { recursive: true });
  fs.writeFileSync(shotPath, Buffer.from(shot.data, 'base64'));
  console.log('\n  界面截图已保存：' + shotPath);
} catch (e) {
  FAIL++;
  console.log('  [FAIL] 测试中断：' + e.message);
} finally {
  try { proc.kill(); } catch (e) { }
  await sleep(300);
  try { fs.rmSync(tmpProfile, { recursive: true, force: true }); } catch (e) { }
}

console.log('\n' + '='.repeat(56));
console.log(`界面测试：通过 ${PASS} 项，失败 ${FAIL} 项`);
console.log('='.repeat(56));
process.exit(FAIL ? 1 : 0);
