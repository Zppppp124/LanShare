/**
 * 验证 --peer 固定对端：B 通过 --peer 指向 A，应自动发现并可互传。
 * 用法：node peer_test.mjs
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PY = process.env.LS_PY || 'python';
const APP = process.argv[2] || path.join(HERE, 'lanshare.py');
const KEY = 'peer-test-key';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ls-peer-'));
const A_DIR = path.join(tmp, 'A');
const B_DIR = path.join(tmp, 'B');
fs.mkdirSync(A_DIR, { recursive: true });
fs.mkdirSync(B_DIR, { recursive: true });
fs.writeFileSync(path.join(A_DIR, '共享 文件.txt'), '这是 A 上的文件\n', 'utf8');
fs.writeFileSync(path.join(A_DIR, '数据.bin'), crypto.randomBytes(1024 * 512));

const sleep = ms => new Promise(r => setTimeout(r, ms));
function start(name, root, port, extra = []) {
  const log = fs.openSync(path.join(tmp, name + '.log'), 'w');
  const isExe = /\.exe$/i.test(APP);
  const bin = isExe ? APP : PY;
  const argv = isExe ? [] : [APP];
  // 固定到测试目录，避免全盘模式扫描真实磁盘
  const p = spawn(bin, [...argv, '--root', root, '--shared-only', '--port', String(port), '--name', name,
    '--no-discovery', '--config', path.join(tmp, 'cfg' + name + '.json'), '--key', KEY, ...extra],
    { stdio: ['ignore', log, log], cwd: HERE });
  return p;
}
async function api(port, method, p, body) {
  const r = await fetch(`http://127.0.0.1:${port}${p}`, {
    method,
    headers: { 'X-LanShare-Key': KEY, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
async function waitUp(port) {
  for (let i = 0; i < 60; i++) { try { await api(port, 'GET', '/api/ping'); return true; } catch { await sleep(300); } }
  return false;
}
let pass = 0, fail = 0;
const check = (n, ok, ex = '') => { if (ok) { pass++; console.log('  [OK]   ' + n); } else { fail++; console.log('  [FAIL] ' + n + (ex ? '  -> ' + ex : '')); } };
const sha = f => crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');

const A = start('节点A', A_DIR, 8871);
await sleep(2500);
// B 用 --peer 指向 A，完全关闭 UDP 发现 —— 只有 --peer 生效才能看到 A
const B = start('节点B', B_DIR, 8872, ['--peer', '127.0.0.1:8871']);
try {
  check('节点A 启动', await waitUp(8871));
  check('节点B 启动', await waitUp(8872));

  let dev = null;
  for (let i = 0; i < 40; i++) {
    const r = await api(8872, 'GET', '/api/devices');
    dev = (r.devices || []).find(d => d.name === '节点A');
    if (dev && dev.online) break;
    await sleep(500);
  }
  check('--peer 自动发现对端（未用 UDP 广播）', dev && dev.online, JSON.stringify(dev));
  check('来源标记为 peer', dev && dev.source === 'peer', dev && dev.source);
  check('记录到正确的地址端口', dev && dev.host === '127.0.0.1' && dev.port === 8871,
    dev ? dev.host + ':' + dev.port : '');

  // 浏览对端目录
  const lst = await api(8872, 'GET', '/api/remote/list?id=' + encodeURIComponent(dev.id) + '&path=');
  check('可浏览 --peer 对端的目录', lst.entries.some(e => e.name === '共享 文件.txt'),
    JSON.stringify(lst.entries.map(e => e.name)));

  // 拉取中文名 + 二进制文件到中文目录
  for (const [src, dest, local] of [['共享 文件.txt', '来自A', '共享 文件.txt'], ['数据.bin', '来自A', '数据.bin']]) {
    const r = await api(8872, 'POST', '/api/pull', { device_id: dev.id, paths: [src], dest });
    let t;
    for (let i = 0; i < 60; i++) {
      t = (await api(8872, 'GET', '/api/tasks')).tasks.find(x => x.id === r.task);
      if (t && ['done', 'error', 'canceled'].includes(t.status)) break;
      await sleep(200);
    }
    const out = path.join(B_DIR, dest, local);
    check(`拉取「${src}」成功`, t && t.status === 'done' && fs.existsSync(out), JSON.stringify(t && t.errors));
    if (fs.existsSync(out)) check(`「${src}」内容一致`, sha(out) === sha(path.join(A_DIR, src)));
  }

  // 反向：A 推送到 B
  const addB = await api(8871, 'POST', '/api/devices', { host: '127.0.0.1', port: 8872 });
  check('A 可添加 B', addB.device && addB.device.name === '节点B', JSON.stringify(addB.device));
  fs.writeFileSync(path.join(A_DIR, '推送测试.txt'), 'push from A\n', 'utf8');
  const s = await api(8871, 'POST', '/api/send', { path: '推送测试.txt', targets: [addB.device.id], dest: '来自A推送' });
  let t;
  for (let i = 0; i < 60; i++) {
    t = (await api(8871, 'GET', '/api/tasks')).tasks.find(x => x.id === s.tasks[0].task);
    if (t && ['done', 'error', 'canceled'].includes(t.status)) break;
    await sleep(200);
  }
  check('A 推送到 B 成功', t && t.status === 'done' &&
    fs.existsSync(path.join(B_DIR, '来自A推送', '推送测试.txt')), JSON.stringify(t && t.errors));
} finally {
  A.kill(); B.kill();
  await sleep(300);
  try { fs.rmSync(tmp, { recursive: true, force: true }); } catch { }
}
console.log('\n' + '='.repeat(56));
console.log(`--peer 测试：通过 ${pass} 项，失败 ${fail} 项`);
console.log('='.repeat(56));
process.exit(fail ? 1 : 0);
