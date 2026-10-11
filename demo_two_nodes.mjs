/**
 * 真实双节点端到端演示：两个独立进程的 LanShare 节点互传文件。
 * 用法：node demo_two_nodes.mjs
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PY = process.env.LS_PY || 'python';
const APP = process.argv[2] || path.join(HERE, 'dist', 'LanShare.py');
const BASE = process.argv[3] || path.join(HERE, 'demo', '双机演示');
const KEY = 'team-2026';
const A_DIR = path.join(BASE, '电脑A');
const B_DIR = path.join(BASE, '电脑B');

fs.rmSync(BASE, { recursive: true, force: true });
fs.mkdirSync(A_DIR, { recursive: true });
fs.mkdirSync(B_DIR, { recursive: true });

// 造数据：中文名文件 + 一个多文件目录 + 一个较大的二进制文件
fs.writeFileSync(path.join(A_DIR, '合同文档 2026.txt'), '这是一份需要传给B电脑的合同。\n'.repeat(20), 'utf8');
fs.mkdirSync(path.join(A_DIR, '项目资料'), { recursive: true });
for (let i = 1; i <= 4; i++) {
  fs.writeFileSync(path.join(A_DIR, '项目资料', `资料${i}.txt`), `资料内容 ${i}\n`.repeat(500), 'utf8');
}
const bigBuf = crypto.randomBytes(8 * 1024 * 1024);
fs.writeFileSync(path.join(A_DIR, '大文件.bin'), bigBuf);

const sleep = ms => new Promise(r => setTimeout(r, ms));

function start(name, root, port) {
  const log = fs.openSync(path.join(BASE, name + '.log'), 'w');
  const isExe = /\.exe$/i.test(APP);
  const bin = isExe ? APP : PY;
  const argv = isExe ? [] : [APP];
  // 用 --shared-only + --root 固定到测试目录，避免全盘模式扫描真实磁盘
  const p = spawn(bin, [...argv, '--root', root, '--shared-only', '--port', String(port), '--name', name,
    '--no-discovery', '--config', path.join(BASE, 'cfg' + name + '.json'), '--key', KEY],
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
  for (let i = 0; i < 60; i++) {
    try { await api(port, 'GET', '/api/ping'); return true; } catch { await sleep(300); }
  }
  return false;
}

function assertAppExists() {
  if (!fs.existsSync(APP)) {
    console.log('找不到程序文件：' + APP);
    console.log('请先运行 python build_single.py 生成 dist/LanShare.py，');
    console.log('或指定其它路径：node demo_two_nodes.mjs <LanShare.py 路径>');
    process.exit(2);
  }
}

const sha = f => crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
let pass = 0, fail = 0;
const check = (n, ok, ex = '') => {
  if (ok) { pass++; console.log('  [OK]   ' + n); }
  else { fail++; console.log('  [FAIL] ' + n + (ex ? '  -> ' + ex : '')); }
};

const A = start('电脑A', A_DIR, 8801);
const B = start('电脑B', B_DIR, 8802);

try {
  assertAppExists();
  check('电脑A 节点启动', await waitUp(8801));
  check('电脑B 节点启动', await waitUp(8802));
  if (fail) {
    for (const n of ['电脑A', '电脑B']) {
      const lf = path.join(BASE, n + '.log');
      if (fs.existsSync(lf)) console.log('  ' + n + ' 日志：' + fs.readFileSync(lf, 'utf8').slice(0, 400));
    }
    throw new Error('节点未能启动，后续步骤跳过');
  }

  const add = await api(8801, 'POST', '/api/devices', { host: '127.0.0.1', port: 8802 });
  check('A 手动添加 B（延迟 ' + add.rtt + ' ms）', add.device.name === '电脑B', JSON.stringify(add.device));
  const bid = add.device.id;

  // 从 B 的视角验证 A 的共享目录标题
  const listB = await api(8802, 'GET', '/api/fs/list?path=');
  check('B 的目录初始为空', listB.entries.length === 0, JSON.stringify(listB.entries));

  // 1) 推送整个目录
  let t0 = Date.now();
  const s1 = await api(8801, 'POST', '/api/send', { path: '项目资料', targets: [bid], dest: '来自A' });
  check('创建目录推送任务（' + s1.files + ' 个文件 / ' + s1.bytes + ' 字节）', !!s1.tasks[0].task);
  let tid = s1.tasks[0].task, t;
  for (let i = 0; i < 100; i++) {
    t = (await api(8801, 'GET', '/api/tasks')).tasks.find(x => x.id === tid);
    if (t && ['done', 'error', 'canceled'].includes(t.status)) break;
    await sleep(200);
  }
  check('目录推送完成', t && t.status === 'done', JSON.stringify(t && t.errors));
  const recvDir = path.join(B_DIR, '来自A', '项目资料');
  check('B 端目录结构正确', fs.existsSync(path.join(recvDir, '资料3.txt')), fs.existsSync(recvDir) ? fs.readdirSync(recvDir).join(',') : '目录不存在');
  check('目录内文件内容一致',
    fs.readdirSync(recvDir).every(f => sha(path.join(recvDir, f)) === sha(path.join(A_DIR, '项目资料', f))));

  // 2) 推送中文名文件
  const s2 = await api(8801, 'POST', '/api/send', { path: '合同文档 2026.txt', targets: [bid], dest: '来自A' });
  tid = s2.tasks[0].task;
  for (let i = 0; i < 100; i++) {
    t = (await api(8801, 'GET', '/api/tasks')).tasks.find(x => x.id === tid);
    if (t && ['done', 'error', 'canceled'].includes(t.status)) break;
    await sleep(200);
  }
  check('中文名文件推送完成', t && t.status === 'done', JSON.stringify(t && t.errors));
  check('中文名文件落盘且内容一致',
    fs.existsSync(path.join(B_DIR, '来自A', '合同文档 2026.txt')) &&
    sha(path.join(B_DIR, '来自A', '合同文档 2026.txt')) === sha(path.join(A_DIR, '合同文档 2026.txt')));

  // 3) 重复推送应全部跳过
  const s3 = await api(8801, 'POST', '/api/send', { path: '项目资料', targets: [bid], dest: '来自A' });
  tid = s3.tasks[0].task;
  for (let i = 0; i < 100; i++) {
    t = (await api(8801, 'GET', '/api/tasks')).tasks.find(x => x.id === tid);
    if (t && ['done', 'error', 'canceled'].includes(t.status)) break;
    await sleep(200);
  }
  check('重复推送全部跳过（skipped=' + (t && t.skipped) + '）', t && t.status === 'done' && t.skipped === 4);

  // 4) 大文件推送 + 速度
  t0 = Date.now();
  const s4 = await api(8801, 'POST', '/api/send', { path: '大文件.bin', targets: [bid], dest: '大文件' });
  tid = s4.tasks[0].task;
  for (let i = 0; i < 200; i++) {
    t = (await api(8801, 'GET', '/api/tasks')).tasks.find(x => x.id === tid);
    if (t && ['done', 'error', 'canceled'].includes(t.status)) break;
    await sleep(150);
  }
  const dt = (Date.now() - t0) / 1000;
  check('8MB 文件推送完成且内容一致',
    t && t.status === 'done' && sha(path.join(B_DIR, '大文件', '大文件.bin')) === sha(path.join(A_DIR, '大文件.bin')));
  console.log('         8MB 耗时 ' + dt.toFixed(2) + 's（' + (8 / dt).toFixed(1) + ' MB/s）');

  // 5) 反向拉取：B 从 A 拉文件
  const addB = await api(8802, 'POST', '/api/devices', { host: '127.0.0.1', port: 8801 });
  const aid = addB.device.id;
  const s5 = await api(8802, 'POST', '/api/pull', { device_id: aid, paths: ['项目资料/资料1.txt'], dest: '从A拉取' });
  tid = s5.task;
  for (let i = 0; i < 100; i++) {
    t = (await api(8802, 'GET', '/api/tasks')).tasks.find(x => x.id === tid);
    if (t && ['done', 'error', 'canceled'].includes(t.status)) break;
    await sleep(200);
  }
  check('B 反向拉取 A 的文件成功',
    t && t.status === 'done' &&
    sha(path.join(B_DIR, '从A拉取', '资料1.txt')) === sha(path.join(A_DIR, '项目资料', '资料1.txt')),
    JSON.stringify(t && t.errors));

  // 6) 只读浏览对方目录
  const remoteList = await api(8802, 'GET', '/api/remote/list?id=' + aid + '&path=');
  check('B 可远程浏览 A 的目录', remoteList.entries.some(e => e.name === '项目资料'), JSON.stringify(remoteList.entries.map(e => e.name)));

  // 7) 本用例两节点都显式用了 --key，所以无密钥应当被拒（验证密钥模式仍有效）
  const deny = await fetch('http://127.0.0.1:8801/api/fs/list?path=');
  check('设置了密钥时，无密钥访问被拒绝', deny.status === 401, String(deny.status));

  console.log('\n  B 端最终目录树：');
  const walk = (d, pre = '    ') => fs.readdirSync(d, { withFileTypes: true }).forEach(e => {
    console.log(pre + (e.isDirectory() ? '📁 ' : '📄 ') + e.name);
    if (e.isDirectory()) walk(path.join(d, e.name), pre + '   ');
  });
  walk(B_DIR);
} finally {
  A.kill(); B.kill();
}

console.log('\n' + '='.repeat(56));
console.log(`双节点演示：通过 ${pass} 项，失败 ${fail} 项`);
console.log('='.repeat(56));
process.exit(fail ? 1 : 0);
