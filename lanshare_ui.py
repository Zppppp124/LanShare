# -*- coding: utf-8 -*-
"""LanShare 内嵌前端页面（HTML/CSS/JS 一体）。"""

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#8b7cd8"/>
<path d="M20 42V22l12 10 12-10v20" fill="none" stroke="#fff" stroke-width="5"
 stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="32" cy="47" r="3.5" fill="#d9f2e3"/></svg>"""

UI_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#ede7fb">
<title>LanShare · 局域网互传</title>
<link rel="icon" href="/favicon.ico">
<style>
:root{
  --bg:#f7f5fc; --bg2:#ffffff; --bg3:#f2eefb; --header:#ede7fb;
  --line:#e6e1f5; --line2:#dcd5f0;
  --fg:#3b3552; --fg2:#8b85a3; --sel:#e4dcfa;
  --accent:#8b7cd8; --accent2:#7566c4;
  --ok:#3fa46a; --warn:#c98a2b; --err:#c4556a; --radius:12px;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0;height:100%}
body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",
 "PingFang SC","Microsoft YaHei",sans-serif;display:flex;flex-direction:column;overflow:hidden}
a{color:var(--accent);text-decoration:none}
button,input,select{font:inherit;color:inherit}
button{cursor:pointer;border:1px solid var(--line2);background:var(--bg3);color:var(--fg);
 padding:7px 12px;border-radius:9px;transition:.15s;white-space:nowrap}
button:hover:not(:disabled){background:var(--bg3);border-color:var(--accent)}
button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--accent2);border-color:var(--accent2);color:#fff}
button.primary:hover:not(:disabled){background:var(--accent)}
button.danger{border-color:#f0d9df;color:var(--err)}
button.danger:hover:not(:disabled){background:#fbeef1}
button.ghost{background:transparent;border-color:transparent;color:var(--fg2)}
button.ghost:hover{background:var(--bg3);color:var(--fg)}
input[type=text],input[type=password],select,input[type=number]{background:var(--bg);
 border:1px solid var(--line2);border-radius:9px;padding:8px 10px;outline:none;width:100%}
input:focus,select:focus{border-color:var(--accent)}
header{display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--bg2);
 border-bottom:1px solid var(--line);flex-wrap:wrap}
.logo{font-weight:700;font-size:16px;letter-spacing:.3px;display:flex;align-items:center;gap:8px}
.logo .dot{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok)}
.node{color:var(--fg2);font-size:12.5px}
.spacer{flex:1}
.tabs{display:flex;gap:4px;background:var(--bg2);padding:0 10px;border-bottom:1px solid var(--line);
 overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{padding:10px 14px;border-bottom:2px solid transparent;color:var(--fg2);cursor:pointer;
 font-size:13.5px;white-space:nowrap;background:none;border-radius:0;border-top:0;border-left:0;border-right:0}
.tab.active{color:var(--fg);border-bottom-color:var(--accent);background:none}
.tab:hover{color:var(--fg);background:none}
main{flex:1;overflow:auto;padding:12px 14px 90px;-webkit-overflow-scrolling:touch}
.panel{max-width:1080px;margin:0 auto}
.bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.crumbs{display:flex;align-items:center;gap:4px;flex-wrap:wrap;flex:1;min-width:200px}
.crumb{padding:4px 8px;border-radius:7px;cursor:pointer;color:var(--fg2)}
.crumb:hover{background:var(--bg3);color:var(--fg)}
.crumb.last{color:var(--fg);font-weight:600}
.sep{color:var(--fg2);opacity:.5}
.list{background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.row{display:flex;align-items:center;gap:10px;padding:9px 12px;border-bottom:1px solid var(--line);
 cursor:default}
.row:last-child{border-bottom:0}
.row:hover{background:var(--bg3)}
.row.sel{background:var(--sel)}
.row .chk{width:18px;height:18px;flex:0 0 auto;accent-color:var(--accent)}
.row .ico{width:24px;text-align:center;flex:0 0 auto;font-size:16px}
.row .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .nm.dir{color:var(--accent2);cursor:pointer;font-weight:500}
.row .nm.dir:hover{text-decoration:underline}
.row .meta{color:var(--fg2);font-size:12.5px;flex:0 0 auto;white-space:nowrap}
.row .acts{display:flex;gap:2px;flex:0 0 auto;opacity:.55}
.row:hover .acts,.row.sel .acts{opacity:1}
@media(hover:none){.row .acts{opacity:1}}
.row .acts button{padding:4px 8px;font-size:12.5px;border-color:transparent;background:transparent}
.row .acts button:hover{background:var(--bg3);border-color:var(--line2)}
.empty{padding:44px 20px;text-align:center;color:var(--fg2)}
.overlay{position:fixed;inset:0;background:rgba(6,8,12,.72);display:flex;align-items:center;
 justify-content:center;padding:18px;z-index:50;backdrop-filter:blur(3px)}
.dialog{background:var(--bg2);border:1px solid var(--line2);border-radius:16px;padding:20px;
 width:100%;max-width:460px;max-height:86vh;overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.dialog h3{margin:0 0 6px;font-size:16px}
.dialog p.sub{margin:0 0 14px;color:var(--fg2);font-size:13px}
.field{margin-bottom:12px}
.field label{display:block;margin-bottom:5px;font-size:12.5px;color:var(--fg2)}
.dialog .foot{display:flex;justify-content:flex-end;gap:8px;margin-top:18px;flex-wrap:wrap}
.dev{display:flex;align-items:center;gap:10px;padding:11px 12px;border:1px solid var(--line);
 border-radius:11px;margin-bottom:8px;background:var(--bg2)}
.dev .st{width:8px;height:8px;border-radius:50%;background:var(--fg2);flex:0 0 auto}
.dev .st.on{background:var(--ok);box-shadow:0 0 7px var(--ok)}
.dev .info{flex:1;min-width:0}
.dev .info b{font-weight:600}
.dev .info div{color:var(--fg2);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pick{display:flex;align-items:center;gap:10px;padding:9px 10px;border:1px solid var(--line);
 border-radius:10px;margin-bottom:6px;cursor:pointer}
.pick:hover{background:var(--bg3)}
.pick input{width:auto;accent-color:var(--accent)}
.task{border:1px solid var(--line);border-radius:11px;padding:11px 12px;margin-bottom:9px;background:var(--bg2)}
.task .t1{display:flex;align-items:center;gap:8px}
.task .t1 b{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.task .t1 .st{margin-left:auto;font-size:12px;color:var(--fg2);flex:0 0 auto}
.prog{height:6px;background:var(--header);border-radius:99px;overflow:hidden;margin:9px 0 6px}
.prog > i{display:block;height:100%;background:linear-gradient(90deg,var(--accent2),#b3a6ea);
 border-radius:99px;transition:width .25s;width:0}
.task .t2{color:var(--fg2);font-size:12.5px;display:flex;gap:10px;flex-wrap:wrap}
.task .t3{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.tag{font-size:11.5px;padding:2px 8px;border-radius:99px;border:1px solid var(--line2);color:var(--fg2)}
.tag.ok{color:var(--ok);border-color:#bfe3cd}.tag.err{color:var(--err);border-color:#f0d9df}
.tag.run{color:var(--accent2);border-color:#d9d0f5}.tag.wait{color:var(--warn);border-color:#ecdcb8}
.bulk{position:fixed;left:0;right:0;bottom:0;background:var(--bg2);border-top:1px solid var(--line2);
 padding:10px 14px;display:none;gap:8px;align-items:center;flex-wrap:wrap;z-index:20;
 box-shadow:0 -8px 24px rgba(0,0,0,.35);padding-bottom:calc(10px + env(safe-area-inset-bottom))}
.bulk.show{display:flex}
.bulk .cnt{color:var(--fg2);font-size:13px;margin-right:4px}
.drop{outline:3px dashed var(--accent);outline-offset:-8px}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:84px;background:var(--bg2);
 border:1px solid var(--line2);border-radius:10px;padding:9px 14px;z-index:99;max-width:90vw;
 box-shadow:0 10px 30px rgba(80,60,140,.18);font-size:13px;opacity:0;transition:.2s;pointer-events:none}
.toast.show{opacity:1}
.hint{color:var(--fg2);font-size:12.5px;margin:6px 0 12px}
.card{background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius);padding:14px;margin-bottom:12px}
.card h4{margin:0 0 10px;font-size:14px}
.kv{display:flex;gap:8px;font-size:13px;padding:5px 0;border-bottom:1px dashed var(--line)}
.kv:last-child{border-bottom:0}
.kv span:first-child{color:var(--fg2);flex:0 0 96px}
.kv span:last-child{word-break:break-all;flex:1}
.up{display:flex;align-items:center;gap:8px;font-size:12.5px}
.switch{display:flex;align-items:center;gap:8px;margin:8px 0;font-size:13px;cursor:pointer}
.switch input{width:auto;accent-color:var(--accent)}
.via{font-size:12px;color:var(--fg2);background:var(--bg3);border:1px solid var(--line2);
 padding:3px 8px;border-radius:8px}
@media(max-width:560px){
  .row .meta.hide-sm{display:none}
  .row{padding:11px 10px;gap:8px}
  main{padding:10px 8px 96px}
  header{padding:9px 10px;gap:8px}
  .node{width:100%;order:3}
}
</style>
</head>
<body>
<header>
  <div class="logo"><span class="dot" id="dot"></span>LanShare</div>
  <div class="node" id="nodeInfo">正在连接…</div>
  <div class="spacer"></div>
  <span class="via" id="netBadge" title="当前浏览的位置">本机</span>
  <button class="ghost" id="btnReload" title="刷新">↻</button>
</header>

<div class="tabs">
  <button class="tab active" data-tab="files">文件</button>
  <button class="tab" data-tab="devices">设备 <span id="devCount"></span></button>
  <button class="tab" data-tab="tasks">传输 <span id="taskCount"></span></button>
  <button class="tab" data-tab="settings">设置</button>
</div>

<main>
  <!-- 文件 -->
  <section class="panel" id="tab-files">
    <div class="bar">
      <select id="selDevice" style="max-width:230px"></select>
      <button id="btnUp">↑ 上级</button>
      <button id="btnRefresh">刷新</button>
      <button id="btnNewDir">新建文件夹</button>
      <button class="primary" id="btnUploadFile">上传文件</button>
      <button id="btnUploadDir">上传文件夹</button>
      <label class="switch" style="margin:0"><input type="checkbox" id="chkHidden">显示隐藏</label>
    </div>
    <div class="crumbs" id="crumbs"></div>
    <div class="hint" id="dropHint">提示：也可以把文件直接拖到页面上传。</div>
    <div class="list" id="fileList"><div class="empty">正在加载…</div></div>
  </section>

  <!-- 设备 -->
  <section class="panel" id="tab-devices" hidden>
    <div class="card">
      <h4>手动添加设备</h4>
      <div class="hint">自动发现依赖 UDP 广播，某些路由器会拦截。此时在另一台设备上查看它的 IP，
      填到下面即可（端口默认 8765）。</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input type="text" id="inHost" placeholder="IP 或 IP:端口，例如 192.168.1.23" style="flex:1;min-width:180px">
        <input type="text" id="inKey" placeholder="对方的群组密钥（可留空）" style="flex:1;min-width:180px">
        <button class="primary" id="btnAddDev">添加</button>
      </div>
    </div>
    <div class="card">
      <h4>局域网内的设备 <span class="tag" id="devTag"></span></h4>
      <div id="devList"><div class="empty">暂无其它设备</div></div>
    </div>
  </section>

  <!-- 传输 -->
  <section class="panel" id="tab-tasks" hidden>
    <div class="bar">
      <button id="btnTaskRefresh">刷新</button>
      <button id="btnClearDone">清除已结束</button>
    </div>
    <div id="taskList"><div class="empty">暂无传输记录</div></div>
  </section>

  <!-- 设置 -->
  <section class="panel" id="tab-settings" hidden>
    <div class="card">
      <h4>本机信息</h4>
      <div id="infoBox"></div>
    </div>
    <div class="card">
      <h4>偏好</h4>
      <div class="field"><label>本机在局域网中显示的名字</label>
        <input type="text" id="cfgName" placeholder="例如：客厅电脑"></div>
      <label class="switch"><input type="checkbox" id="cfgAuto"> 允许其它设备直接写入本机（关闭后需在“传输”页逐次确认）</label>
      <div class="field"><label>接收文件时的子目录（留空表示对方原样指定）</label>
        <input type="text" id="cfgIncoming" placeholder="例如：LanShare 接收"></div>
      <button class="primary" id="btnSaveCfg">保存设置</button>
    </div>
    <div class="card">
      <h4>群组密钥</h4>
      <div class="hint">同一局域网内，多台设备填同一个密钥即可互相信任、互传文件。
      修改后需要重启服务，并让其它设备同步修改。</div>
      <div class="field"><input type="text" id="cfgKey" placeholder="群组密钥"></div>
      <button id="btnSaveKey">更新密钥</button>
    </div>
    <div class="card">
      <h4>命令行</h4>
      <div class="hint">更多选项（换端口、指定共享目录、关闭密钥等）：</div>
      <pre style="background:var(--bg);padding:10px;border-radius:9px;overflow:auto;font-size:12.5px">python lanshare.py --root "D:\共享" --name 客厅电脑 --port 8765</pre>
    </div>
  </section>
</main>

<div class="bulk" id="bulk">
  <span class="cnt" id="bulkCnt">已选 0 项</span>
  <button class="primary" id="bulkSend">发送到设备</button>
  <button id="bulkPull">拉取到本机</button>
  <button id="bulkDownload">下载</button>
  <button class="danger" id="bulkDelete">删除</button>
  <button class="ghost" id="bulkClear">取消选择</button>
</div>

<div class="toast" id="toast"></div>
<input type="file" id="fileInput" multiple hidden>
<input type="file" id="dirInput" webkitdirectory directory multiple hidden>

<script>
'use strict';
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

let KEY = localStorage.getItem('ls_key') || '';
let NODE = {};            // /api/info 返回
let DEVICES = [];
let CUR = { dev: '', path: '' };   // 当前浏览位置；dev='' 表示本机
let ENTRIES = [];
let SEL = new Set();
let TASKS = [];
let UPJOBS = [];          // 本页上传进度
let TABS = 'files';

/* ---------------- 基础 ---------------- */
function toast(msg, ms) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.classList.remove('show'), ms || 2600);
}
function fmtSize(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' B';
  const u = ['KB', 'MB', 'GB', 'TB']; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return n.toFixed(n >= 100 ? 0 : 2) + ' ' + u[i];
}
function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000), n = new Date();
  const p = x => String(x).padStart(2, '0');
  const sameDay = d.toDateString() === n.toDateString();
  if (sameDay) return p(d.getHours()) + ':' + p(d.getMinutes());
  return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}
function fmtDur(s) {
  s = Math.max(0, Math.floor(s));
  if (s < 60) return s + ' 秒';
  if (s < 3600) return Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒';
  return Math.floor(s / 3600) + ' 小时 ' + Math.floor(s % 3600 / 60) + ' 分';
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function icon(e) {
  if (e.drive) return '💽';
  if (e.is_dir) return '📁';
  const x = (e.ext || '').toLowerCase();
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'heic'].includes(x)) return '🖼️';
  if (['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'ts'].includes(x)) return '🎬';
  if (['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg'].includes(x)) return '🎵';
  if (['zip', 'rar', '7z', 'tar', 'gz', 'xz', 'iso'].includes(x)) return '🗜️';
  if (['pdf'].includes(x)) return '📕';
  if (['doc', 'docx', 'odt'].includes(x)) return '📘';
  if (['xls', 'xlsx', 'csv', 'ods'].includes(x)) return '📗';
  if (['ppt', 'pptx', 'odp'].includes(x)) return '📙';
  if (['exe', 'msi', 'apk', 'dmg', 'bat', 'sh'].includes(x)) return '⚙️';
  if (['txt', 'md', 'log', 'json', 'xml', 'py', 'js', 'ts', 'html', 'css', 'c', 'cpp', 'java', 'go', 'rs'].includes(x)) return '📄';
  return '📦';
}

/* HTTP 头只能是 ISO-8859-1（latin1）字符。
   密钥允许用户随便填，万一填了中文，直接塞进 header 会让浏览器在
   fetch() 构造阶段就抛 “String contains non ISO-8859-1 code point”，
   表现为整个界面「网络不可达」。所以这里统一编码后再发，服务端再解开。 */
function b64utf8(str) {
  try {
    const bytes = new TextEncoder().encode(str);
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  } catch (e) {
    return '';
  }
}
function headerSafe(v) {
  v = String(v == null ? '' : v);
  return /^[\x20-\x7E]*$/.test(v) ? v : ('b64:' + b64utf8(v));
}

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({}, opts.headers || {});
  if (KEY) headers['X-LanShare-Key'] = headerSafe(KEY);
  if (opts.body && typeof opts.body === 'string') headers['Content-Type'] = 'application/json; charset=utf-8';
  let r;
  try {
    r = await fetch(path, Object.assign({}, opts, { headers }));
  } catch (e) {
    // 兜底：万一还是被头编码挡住，清掉本地存的密钥并提示，避免整个界面卡死
    if (/ISO-8859-1|non ISO|latin1/i.test(String(e && e.message))) {
      KEY = '';
      try { localStorage.removeItem('ls_key'); } catch (_) { }
      throw new Error('本机浏览器里存的访问密码含有非法字符，已自动清除，请刷新页面重试');
    }
    throw new Error('网络不可达：' + e.message);
  }
  if (r.status === 401) { showGate(); throw new Error('需要群组密钥'); }
  let data;
  try { data = await r.json(); } catch (e) { throw new Error('服务返回异常（HTTP ' + r.status + '）'); }
  if (!data.ok) throw new Error(data.error || ('请求失败 HTTP ' + r.status));
  return data;
}
const jpost = (p, o) => api(p, { method: 'POST', body: JSON.stringify(o || {}) });
const jput = (p, o) => api(p, { method: 'PUT', body: JSON.stringify(o || {}) });

/* ---------------- 密钥门 ---------------- */
function showGate() {
  if ($('#gate')) return;
  const d = document.createElement('div');
  d.className = 'overlay'; d.id = 'gate';
  d.innerHTML = `<div class="dialog">
    <h3>需要群组密钥</h3>
    <p class="sub">这台设备的共享目录受密钥保护。请输入它的「群组密钥」（运行 lanshare.py 时控制台会显示）。</p>
    <div class="field"><input type="password" id="gateKey" placeholder="群组密钥"></div>
    <label class="switch"><input type="checkbox" id="gateRemember" checked> 记住密钥（仅保存在本机浏览器）</label>
    <div class="foot"><button class="primary" id="gateOk">进入</button></div>
  </div>`;
  document.body.appendChild(d);
  $('#gateOk').onclick = () => {
    const v = $('#gateKey').value.trim();
    if (!v) { toast('请输入密钥'); return; }
    KEY = v;
    if ($('#gateRemember').checked) {
      localStorage.setItem('ls_key', v);
      document.cookie = 'ls_key=' + encodeURIComponent(v) + ';path=/;max-age=31536000;SameSite=Lax';
    }
    d.remove();
    boot();
  };
  $('#gateKey').addEventListener('keydown', e => { if (e.key === 'Enter') $('#gateOk').click(); });
  setTimeout(() => $('#gateKey').focus(), 60);
}

/* ---------------- 启动 ---------------- */
async function boot() {
  try {
    NODE = await api('/api/info');
  } catch (e) {
    if (String(e.message).includes('密钥')) return;
    $('#nodeInfo').textContent = '连接失败：' + e.message;
    return;
  }
  $('#dot').style.background = 'var(--ok)';
  $('#nodeInfo').textContent = `${NODE.name} · ${NODE.root} · ${NODE.local_ips.join(' / ')}:${NODE.port}`;
  document.title = 'LanShare · ' + NODE.name;
  $('#cfgName').value = NODE.name || '';
  $('#cfgAuto').checked = !!NODE.auto_accept;
  $('#cfgIncoming').value = NODE.incoming_dir || '';
  $('#cfgKey').value = NODE.key || '';
  renderInfo();
  buildDeviceSelect();
  refreshDevices();
  loadFiles();
  refreshTasks();
  setInterval(refreshDevices, 5000);
  setInterval(() => { if (TABS === 'tasks') refreshTasks(); }, 1500);
  setInterval(() => { if (TABS === 'files' && !CUR.dev) loadFiles(true); }, 10000);
}

function renderInfo() {
  const d = NODE.disk;
  $('#infoBox').innerHTML = `
    <div class="kv"><span>本机名称</span><span>${esc(NODE.name)}</span></div>
    <div class="kv"><span>可访问范围</span><span>${NODE.allow_whole_disk
      ? '<b style="color:var(--warn)">本机全部磁盘</b> ' + (NODE.drives || []).map(esc).join('、')
      : esc(NODE.root)}</span></div>
    <div class="kv"><span>访问地址</span><span>${NODE.urls.map(u => esc(u)).join('<br>')}</span></div>
    <div class="kv"><span>监听端口</span><span>${NODE.port}</span></div>
    <div class="kv"><span>磁盘空间</span><span>${d ? '剩余 ' + fmtSize(d.free) + ' / 共 ' + fmtSize(d.total) : '未知'}</span></div>
    <div class="kv"><span>访问密码</span><span>${NODE.auth_required ? '已开启' : '无（局域网内直接访问）'}</span></div>
    <div class="kv"><span>运行时长</span><span>${fmtDur(NODE.uptime)}</span></div>
    <div class="kv"><span>版本</span><span>${esc(NODE.app)} ${esc(NODE.version)} · ${esc(NODE.platform)}</span></div>`;
}

/* ---------------- 设备 ---------------- */
function buildDeviceSelect() {
  const sel = $('#selDevice');
  sel.innerHTML = '<option value="">🖥️ 本机 · ' + esc(NODE.root || '') + '</option>' +
    DEVICES.map(d => `<option value="${esc(d.id)}">${d.online ? '🟢' : '⚪'} ${esc(d.name)} · ${esc(d.host || '')}:${d.port}${d.whole_disk ? ' · 全盘' : ''}</option>`).join('');
  sel.value = CUR.dev;
}
async function refreshDevices() {
  try {
    const r = await api('/api/devices');
    DEVICES = r.devices || [];
  } catch (e) { return; }
  const online = DEVICES.filter(d => d.online).length;
  $('#devCount').textContent = online ? online : '';
  $('#devTag').textContent = `在线 ${online} / 共 ${DEVICES.length}`;
  buildDeviceSelect();
  renderDevices();
}
function renderDevices() {
  const box = $('#devList');
  if (!DEVICES.length) { box.innerHTML = '<div class="empty">暂无其它设备<br><span style="font-size:12px">确认两台设备在同一局域网，或手动填 IP 添加</span></div>'; return; }
  box.innerHTML = DEVICES.map(d => `
    <div class="dev">
      <span class="st ${d.online ? 'on' : ''}"></span>
      <div class="info">
        <b>${esc(d.name)}</b>
        <div>${esc(d.host || '')}:${d.port} · 共享「${esc(d.label || '?')}」${d.version ? ' · v' + esc(d.version) : ''}${d.source === 'manual' ? ' · 手动' : ''}</div>
      </div>
      <button data-ping="${esc(d.id)}">测试</button>
      <button class="primary" data-open="${esc(d.id)}">浏览</button>
    </div>`).join('');
  box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => {
    CUR = { dev: b.dataset.open, path: '' };
    SEL.clear();
    switchTab('files');
    loadFiles();
  });
  box.querySelectorAll('[data-ping]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api('/api/devices/ping?id=' + encodeURIComponent(b.dataset.ping));
      toast('连接正常，延迟 ' + r.rtt + ' ms');
      await refreshDevices();
    } catch (e) { toast('连接失败：' + e.message, 4000); }
    finally { b.disabled = false; }
  });
}

/* ---------------- 文件列表 ---------------- */
async function loadFiles(quiet) {
  const dev = CUR.dev, path = CUR.path;
  if (!quiet) $('#fileList').innerHTML = '<div class="empty">正在加载…</div>';
  const url = dev
    ? '/api/remote/list?id=' + encodeURIComponent(dev) + '&path=' + encodeURIComponent(path) + ($('#chkHidden').checked ? '&hidden=1' : '')
    : '/api/fs/list?path=' + encodeURIComponent(path) + ($('#chkHidden').checked ? '&hidden=1' : '');
  let r;
  try {
    r = await api(url);
  } catch (e) {
    $('#fileList').innerHTML = '<div class="empty">加载失败：' + esc(e.message) + '</div>';
    return;
  }
  if (dev !== CUR.dev || path !== CUR.path) return;   // 位置已变，丢弃
  ENTRIES = r.entries || [];
  const label = dev ? (DEVICES.find(d => d.id === dev) || {}).name || '远程设备' : '本机 · ' + r.root;
  $('#netBadge').textContent = label + ' : /' + path;
  $('#dropHint').style.display = dev ? 'none' : '';
  $('#btnUploadFile').disabled = $('#btnUploadDir').disabled = $('#btnNewDir').disabled = !!dev;
  renderCrumbs(r.parents || [], dev);
  renderFiles();
}
function renderCrumbs(parents, dev) {
  const label = dev ? (DEVICES.find(d => d.id === dev) || {}).name || '远程' : '本机';
  let html = `<span class="crumb" data-p="">${dev ? '🌐' : '🖥️'} ${esc(label)}</span>`;
  parents.forEach((p, i) => {
    html += `<span class="sep">/</span><span class="crumb ${i === parents.length - 1 ? 'last' : ''}" data-p="${esc(p.path)}">${esc(p.name)}</span>`;
  });
  const box = $('#crumbs');
  box.innerHTML = html;
  box.querySelectorAll('.crumb').forEach(c => c.onclick = () => {
    CUR.path = c.dataset.p; SEL.clear(); loadFiles();
  });
}
function renderFiles() {
  const box = $('#fileList');
  if (!ENTRIES.length) {
    box.innerHTML = '<div class="empty">这个文件夹是空的' + (CUR.dev ? '' : '<br><span style="font-size:12px">拖文件到页面上传，或点右上角「上传文件」</span>') + '</div>';
    return;
  }
  const atComputerRoot = !CUR.dev && !CUR.path && NODE.allow_whole_disk;
  box.innerHTML = ENTRIES.map((e, i) => {
    const rel = (CUR.path ? CUR.path + '/' : '') + e.name;
    const sel = SEL.has(rel);
    let meta1, meta2;
    if (e.drive) {
      meta1 = e.total ? (fmtSize(e.free) + ' 可用 / ' + fmtSize(e.total)) : '磁盘';
      meta2 = e.label ? esc(e.label) : '';
    } else {
      meta1 = e.is_dir ? (e.count === null || e.count === undefined ? '文件夹' : e.count + ' 项') : fmtSize(e.size);
      meta2 = fmtTime(e.mtime);
    }
    // 「此电脑」根上不给盘符提供重命名/删除/发送
    const ops = e.drive ? '' : `
        ${e.is_dir ? '' : `<button data-dl="${i}" title="下载">⤓</button>`}
        ${CUR.dev ? '' : `<button data-rn="${i}" title="重命名">✎</button>
        <button data-del="${i}" title="删除">🗑</button>`}
        <button data-send="${i}" title="发送到其它设备">➤</button>`;
    return `<div class="row ${sel ? 'sel' : ''}" data-i="${i}">
      <input type="checkbox" class="chk" ${sel ? 'checked' : ''} data-chk="${i}" ${e.drive ? 'disabled' : ''}>
      <span class="ico">${icon(e)}</span>
      <span class="nm ${e.is_dir ? 'dir' : ''}" data-open="${i}" title="${esc(e.name)}">${esc(e.name)}${e.drive && e.label ? ' <span style="color:var(--fg2);font-size:12px">' + esc(e.label) + '</span>' : ''}</span>
      <span class="meta hide-sm">${meta1}</span>
      <span class="meta hide-sm">${meta2}</span>
      <span class="acts">${ops}</span>
    </div>`;
  }).join('') + (atComputerRoot
    ? '<div class="hint" style="padding:12px 14px;margin:0">👆 点磁盘名进入；选中文件后可发送到其它设备或下载。</div>' : '');
  box.querySelectorAll('[data-chk]').forEach(el => el.onchange = () => {
    const e = ENTRIES[+el.dataset.chk], rel = relOf(e);
    el.checked ? SEL.add(rel) : SEL.delete(rel);
    el.closest('.row').classList.toggle('sel', el.checked);
    updateBulk();
  });
  box.querySelectorAll('[data-open]').forEach(el => el.onclick = () => {
    const e = ENTRIES[+el.dataset.open];
    if (!e.is_dir) return;
    CUR.path = relOf(e); SEL.clear(); loadFiles();
  });
  box.querySelectorAll('[data-dl]').forEach(el => el.onclick = () => downloadEntry(ENTRIES[+el.dataset.dl]));
  box.querySelectorAll('[data-rn]').forEach(el => el.onclick = () => renameEntry(ENTRIES[+el.dataset.rn]));
  box.querySelectorAll('[data-del]').forEach(el => el.onclick = () => deleteEntries([ENTRIES[+el.dataset.del]]));
  box.querySelectorAll('[data-send]').forEach(el => el.onclick = () => sendDialog([ENTRIES[+el.dataset.send]]));
}
function relOf(e) { return (CUR.path ? CUR.path + '/' : '') + e.name; }
function selEntries() { return ENTRIES.filter(e => SEL.has(relOf(e))); }
function updateBulk() {
  const n = SEL.size;
  $('#bulkCnt').textContent = '已选 ' + n + ' 项';
  $('#bulk').classList.toggle('show', n > 0);
  $('#bulkPull').style.display = CUR.dev ? '' : 'none';
  $('#bulkDelete').style.display = CUR.dev ? 'none' : '';
  $('#bulkDownload').style.display = (CUR.dev || n !== 1) ? 'none' : '';
}

/* ---------------- 下载 / 上传 ---------------- */
function downloadEntry(e) {
  const rel = relOf(e);
  if (e.is_dir) { toast('文件夹请先进入后用「发送到设备」或在对方设备上拉取'); return; }
  const a = document.createElement('a');
  a.href = '/api/fs/download?path=' + encodeURIComponent(rel) + (KEY ? '&key=' + encodeURIComponent(KEY) : '');
  a.download = e.name;
  document.body.appendChild(a); a.click(); a.remove();
  toast('开始下载 ' + e.name);
}

function joinRel(base, name) { return base ? base.replace(/\/+$/, '') + '/' + name : name; }

async function uploadList(files) {
  if (CUR.dev) { toast('远程目录不支持上传，请切回本机'); return; }
  if (!CUR.path && NODE.allow_whole_disk) { toast('请先进入某个磁盘或文件夹再上传'); return; }
  const list = Array.from(files);
  if (!list.length) return;
  const total = list.reduce((s, f) => s + f.size, 0);
  const job = { id: 'up' + Date.now(), name: list.length > 1 ? (list.length + ' 个文件') : list[0].name, total, done: 0, ok: 0, skip: 0, fail: 0, stop: false, speed: 0 };
  UPJOBS.unshift(job);
  renderTasks();
  let bytes0 = 0, t0 = Date.now();
  for (const f of list) {
    if (job.stop) break;
    const rel = joinRel(CUR.path, f.webkitRelativePath || f.name);
    try {
      let offset = 0;
      try {
        const st = await api('/api/fs/stat?path=' + encodeURIComponent(rel));
        if (st.exists && !st.is_dir) {
          if (st.size === f.size && f.size > 0) { job.skip++; job.done += f.size; job.ok++; renderTasks(); continue; }
          if (st.size > 0 && st.size < f.size) offset = st.size;
        }
      } catch (e) { }
      await uploadOne(f, rel, offset, job, () => { bytes0 = job.done; t0 = Date.now(); });
      job.ok++;
    } catch (e) {
      job.fail++;
      toast('上传失败 ' + f.name + '：' + e.message, 4200);
    }
    renderTasks();
  }
  job.finished = Date.now();
  loadFiles(true);
  toast(`上传结束：成功 ${job.ok}${job.skip ? '，跳过 ' + job.skip : ''}${job.fail ? '，失败 ' + job.fail : ''}`);
}

function uploadOne(file, rel, startOffset, job, onFirst) {
  return new Promise((resolve, reject) => {
    const CH = 4 * 1024 * 1024;
    let offset = startOffset, tries = 0;
    if (startOffset) job.done += startOffset;
    if (startOffset && onFirst) onFirst();
    const step = () => {
      if (job.stop) return reject(new Error('已取消'));
      if (offset >= file.size) return resolve();
      const blob = file.slice(offset, Math.min(offset + CH, file.size));
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', '/api/fs/upload?path=' + encodeURIComponent(rel) + '&offset=' + offset);
      if (KEY) xhr.setRequestHeader('X-LanShare-Key', headerSafe(KEY));
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-LanShare-Total', String(file.size));
      let last = 0;
      xhr.upload.onprogress = ev => { job.done += ev.loaded - last; last = ev.loaded; renderTasks(); };
      xhr.onload = () => {
        if (xhr.status >= 400) {
          let msg = 'HTTP ' + xhr.status;
          try { msg = JSON.parse(xhr.responseText).error || msg; } catch (e) { }
          if (xhr.status === 401) showGate();
          return reject(new Error(msg));
        }
        job.done += blob.size - last;
        offset += blob.size;
        tries = 0;
        step();
      };
      xhr.onerror = () => {
        job.done -= last;
        if (++tries <= 3) { setTimeout(step, 700 * tries); }
        else reject(new Error('网络中断'));
      };
      xhr.send(blob);
    };
    step();
  });
}

/* ---------------- 文件操作 ---------------- */
async function newDir() {
  const name = prompt('新建文件夹名称：');
  if (!name) return;
  try {
    await jpost('/api/fs/mkdir', { path: joinRel(CUR.path, name) });
    toast('已创建 ' + name); loadFiles(true);
  } catch (e) { toast('创建失败：' + e.message); }
}
async function renameEntry(e) {
  const name = prompt('重命名为：', e.name);
  if (!name || name === e.name) return;
  try {
    await jpost('/api/fs/rename', { path: relOf(e), name });
    toast('已重命名'); loadFiles(true);
  } catch (err) { toast('重命名失败：' + err.message); }
}
async function deleteEntries(entries) {
  if (CUR.dev) { toast('远程文件请到该设备的网页上删除'); return; }
  const names = entries.map(e => e.name);
  const msg = entries.length === 1
    ? `确定删除「${names[0]}」${entries[0].is_dir ? '及其全部内容' : ''}？此操作不可撤销。`
    : `确定删除选中的 ${entries.length} 项？此操作不可撤销。`;
  if (!confirm(msg)) return;
  let ok = 0;
  for (const e of entries) {
    try { await jpost('/api/fs/delete', { path: relOf(e) }); ok++; }
    catch (err) { toast('删除失败 ' + e.name + '：' + err.message, 4000); }
  }
  SEL.clear(); updateBulk();
  toast(`已删除 ${ok} 项`); loadFiles(true);
}

/* ---------------- 发送 / 拉取 ---------------- */
function sendDialog(entries) {
  if (!entries.length) return;
  if (!CUR.dev && !CUR.path && NODE.allow_whole_disk) {
    toast('这是「此电脑」根目录，请先进入某个磁盘再发送'); return;
  }
  const cands = DEVICES.filter(d => d.online);
  if (!cands.length) { toast('没有在线的其它设备，请先到「设备」页确认'); return; }
  const paths = entries.map(e => relOf(e));
  const title = entries.length === 1 ? entries[0].name : entries.length + ' 个项目';
  const totalSize = entries.reduce((s, e) => s + (e.is_dir ? 0 : e.size), 0);
  const d = document.createElement('div');
  d.className = 'overlay';
  d.innerHTML = `<div class="dialog">
    <h3>发送「${esc(title)}」</h3>
    <p class="sub">${entries.length === 1 ? (entries[0].is_dir ? '这是一个文件夹，将连同其中的所有文件一起发送。' : fmtSize(entries[0].size)) : '共 ' + entries.length + ' 项' + (totalSize ? '（' + fmtSize(totalSize) + '）' : '')}</p>
    <div class="field"><label>接收设备（可多选）</label>
      ${cands.map(c => `<label class="pick"><input type="checkbox" value="${esc(c.id)}"><b>${esc(c.name)}</b>
        <span style="color:var(--fg2);font-size:12.5px">${esc(c.host)}:${c.port}</span></label>`).join('')}
    </div>
    <div class="field"><label>对方保存到哪个目录（留空=对方的默认接收目录）</label>
      <input type="text" id="sdDest" placeholder="例如：D:/接收 或留空"></div>
    <div class="field"><label>同名文件处理</label>
      <select id="sdOver"><option value="skip">同名同大小则跳过（推荐，可断点续传）</option>
      <option value="overwrite">总是覆盖</option></select></div>
    <div class="foot"><button id="sdCancel">取消</button><button class="primary" id="sdGo">开始发送</button></div>
  </div>`;
  document.body.appendChild(d);
  d.querySelector('#sdCancel').onclick = () => d.remove();
  d.querySelector('#sdGo').onclick = async () => {
    const targets = Array.from(d.querySelectorAll('input[type=checkbox]:checked')).map(x => x.value);
    if (!targets.length) { toast('请选择至少一台设备'); return; }
    const dest = d.querySelector('#sdDest').value.trim();
    const overwrite = d.querySelector('#sdOver').value;
    d.remove();
    try {
      // 每个选中的条目各自作为一个发送任务，避免多选时误传整个当前目录
      let files = 0, bytes = 0, tasks = 0;
      for (const p of paths) {
        const r = await jpost('/api/send', {
          path: p, targets, dest, overwrite, src_device: CUR.dev || ''
        });
        files += r.files || 0; bytes += r.bytes || 0;
        tasks += (r.tasks || []).filter(t => t.task).length;
      }
      toast(`已创建 ${tasks} 个发送任务，共 ${files} 个文件 / ${fmtSize(bytes)}`);
      switchTab('tasks'); refreshTasks();
    } catch (e) { toast('发送失败：' + e.message, 4200); }
  };
}

async function pullToLocal(entries) {
  if (!CUR.dev) return;
  const paths = entries.map(e => relOf(e));
  const def = NODE.allow_whole_disk
    ? ((NODE.drives && NODE.drives[0] ? NODE.drives[0] : 'C:') + '/' + (NODE.incoming_dir || 'LanShare 接收'))
    : (NODE.incoming_dir || '');
  const dest = prompt('拉取到本机的哪个目录：', def);
  if (dest === null) return;
  try {
    const r = await jpost('/api/pull', { device_id: CUR.dev, paths, dest: dest.trim() });
    toast('已开始拉取 ' + r.count + ' 项');
    switchTab('tasks'); refreshTasks();
  } catch (e) { toast('拉取失败：' + e.message, 4200); }
}

/* ---------------- 传输任务 ---------------- */
async function refreshTasks() {
  let r;
  try { r = await api('/api/tasks'); } catch (e) { return; }
  TASKS = r.tasks || [];
  const running = TASKS.filter(t => t.status === 'running').length + UPJOBS.filter(j => !j.finished).length;
  $('#taskCount').textContent = running ? running : '';
  renderTasks();
}
function taskKind(t) {
  if (t.kind === 'send') return { t: '发送', cls: 'run', ico: '➤' };
  if (t.kind === 'receive') return { t: '接收', cls: 'run', ico: '⤓' };
  if (t.kind === 'pull') return { t: '拉取', cls: 'run', ico: '⤒' };
  return { t: '任务', cls: '', ico: '•' };
}
function statusTag(st) {
  const m = { running: ['进行中', 'run'], done: ['完成', 'ok'], error: ['出错', 'err'],
              paused: ['已暂停', 'wait'], canceled: ['已取消', 'wait'], pending: ['待确认', 'wait'] };
  const x = m[st] || [st, ''];
  return `<span class="tag ${x[1]}">${x[0]}</span>`;
}
function renderTasks() {
  const box = $('#taskList');
  if (!TASKS.length && !UPJOBS.length) { box.innerHTML = '<div class="empty">暂无传输记录</div>'; return; }
  let html = '';
  UPJOBS.forEach(j => {
    const pct = j.total ? Math.min(100, j.done * 100 / j.total) : (j.finished ? 100 : 0);
    html += `<div class="task">
      <div class="t1">⬆ <b>${esc(j.name)}</b>
        <span class="st">${j.finished ? '<span class="tag ok">完成</span>' : statusTag('running')}</span></div>
      <div class="prog"><i style="width:${pct}%"></i></div>
      <div class="t2"><span>${fmtSize(j.done)} / ${fmtSize(j.total)}</span><span>${pct.toFixed(1)}%</span>
        ${j.skip ? '<span>跳过 ' + j.skip + '</span>' : ''}${j.fail ? '<span style="color:var(--err)">失败 ' + j.fail + '</span>' : ''}
        <span>上传到本机</span></div>
      ${j.finished ? '' : '<div class="t3"><button data-upstop="' + j.id + '">取消</button></div>'}
    </div>`;
  });
  TASKS.forEach(t => {
    const k = taskKind(t);
    const pct = t.status === 'done' ? 100 : Math.max(0, Math.min(100, t.progress || (t.total_bytes ? t.sent_bytes * 100 / t.total_bytes : 0)));
    const who = t.kind === 'send' ? ('→ ' + esc(t.remote_name || '')) : (t.remote_name ? ('← ' + esc(t.remote_name)) : '');
    html += `<div class="task">
      <div class="t1">${k.ico} <b>${esc(t.current || t.path || '—')}</b>
        <span class="st">${statusTag(t.status)}</span></div>
      <div class="prog"><i style="width:${pct}%"></i></div>
      <div class="t2">
        <span>${fmtSize(t.sent_bytes)} / ${fmtSize(t.total_bytes)}</span>
        <span>${t.done_files || 0}/${t.total_files || 0} 个文件</span>
        <span>${pct.toFixed(1)}%</span>
        ${t.speed ? '<span>' + fmtSize(t.speed) + '/s</span>' : ''}
        <span>${k.t} ${who}</span>
        ${t.skipped ? '<span>跳过 ' + t.skipped + '</span>' : ''}
        <span>${fmtTime(t.created)}</span>
      </div>
      ${t.note ? '<div class="t2" style="margin-top:6px;color:var(--warn)">' + esc(t.note) + '</div>' : ''}
      ${(t.errors && t.errors.length) ? '<div class="t2" style="margin-top:6px;color:var(--err);display:block">' + t.errors.slice(0, 3).map(esc).join('<br>') + '</div>' : ''}
      <div class="t3">
        ${t.status === 'running' ? `<button data-tp="pause" data-id="${t.id}">暂停</button><button data-tp="cancel" data-id="${t.id}">取消</button>` : ''}
        ${t.status === 'paused' ? `<button data-tp="resume" data-id="${t.id}">继续</button><button data-tp="cancel" data-id="${t.id}">取消</button>` : ''}
        ${t.status === 'pending' ? `<button class="primary" data-tp="accept" data-id="${t.id}">接收</button><button data-tp="reject" data-id="${t.id}">拒绝</button>` : ''}
        ${['done', 'error', 'canceled'].includes(t.status) ? `<button data-tp="delete" data-id="${t.id}">移除记录</button>` : ''}
        ${t.status === 'error' ? `<button data-retry="${t.id}">重试</button>` : ''}
      </div>
    </div>`;
  });
  box.innerHTML = html;
  box.querySelectorAll('[data-tp]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try { await jpost('/api/tasks', { action: b.dataset.tp, id: b.dataset.id }); refreshTasks(); }
    catch (e) { toast('操作失败：' + e.message); b.disabled = false; }
  });
  box.querySelectorAll('[data-upstop]').forEach(b => b.onclick = () => {
    const j = UPJOBS.find(x => x.id === b.dataset.upstop); if (j) { j.stop = true; j.finished = Date.now(); renderTasks(); }
  });
  box.querySelectorAll('[data-retry]').forEach(b => b.onclick = async () => {
    const t = TASKS.find(x => x.id === b.dataset.retry);
    if (!t) return;
    b.disabled = true;
    try {
      if (t.kind === 'send' && t.remote_id) {
        await jpost('/api/send', { path: t.path, targets: [t.remote_id], dest: t.dest_base || '', overwrite: t.overwrite || 'skip', src_device: t.src_device || '' });
      } else if (t.kind === 'pull' && t.paths) {
        await jpost('/api/pull', { device_id: t.remote_id, paths: t.paths, dest: t.dest || '' });
      } else { toast('该任务无法自动重试'); b.disabled = false; return; }
      toast('已重新开始'); refreshTasks();
    } catch (e) { toast('重试失败：' + e.message); b.disabled = false; }
  });
}

/* ---------------- 交互 ---------------- */
function switchTab(name) {
  TABS = name;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  ['files', 'devices', 'tasks', 'settings'].forEach(k => { $('#tab-' + k).hidden = (k !== name); });
  if (name === 'tasks') refreshTasks();
  if (name === 'devices') refreshDevices();
  if (name === 'settings') renderInfo();
}

$('#selDevice').onchange = e => { CUR = { dev: e.target.value, path: '' }; SEL.clear(); updateBulk(); loadFiles(); };
$('#btnRefresh').onclick = () => loadFiles();
$('#btnUp').onclick = () => {
  if (!CUR.path) return toast('已经在最上层了');
  CUR.path = CUR.path.split('/').slice(0, -1).join('/'); SEL.clear(); updateBulk(); loadFiles();
};
$('#btnNewDir').onclick = newDir;
$('#btnUploadFile').onclick = () => { CUR.dev ? toast('远程目录不支持上传') : $('#fileInput').click(); };
$('#btnUploadDir').onclick = () => { CUR.dev ? toast('远程目录不支持上传') : $('#dirInput').click(); };
$('#fileInput').onchange = e => { uploadList(e.target.files); e.target.value = ''; };
$('#dirInput').onchange = e => { uploadList(e.target.files); e.target.value = ''; };
$('#chkHidden').onchange = () => loadFiles();
$('#btnReload').onclick = () => { refreshDevices(); loadFiles(); refreshTasks(); toast('已刷新'); };
$('#bulkClear').onclick = () => { SEL.clear(); renderFiles(); updateBulk(); };
$('#bulkSend').onclick = () => sendDialog(selEntries());
$('#bulkPull').onclick = () => pullToLocal(selEntries());
$('#bulkDownload').onclick = () => { const e = selEntries()[0]; if (e) downloadEntry(e); };
$('#bulkDelete').onclick = () => deleteEntries(selEntries());
$('#btnTaskRefresh').onclick = refreshTasks;
$('#btnClearDone').onclick = async () => {
  const done = TASKS.filter(t => ['done', 'error', 'canceled'].includes(t.status));
  if (!done.length) { UPJOBS = UPJOBS.filter(j => !j.finished); renderTasks(); return; }
  for (const t of done) { try { await jpost('/api/tasks', { action: 'delete', id: t.id }); } catch (e) { } }
  UPJOBS = UPJOBS.filter(j => !j.finished);
  refreshTasks();
};
$('#btnSaveCfg').onclick = async () => {
  try {
    await jpost('/api/config', {
      name: $('#cfgName').value.trim(),
      auto_accept: $('#cfgAuto').checked,
      incoming_dir: $('#cfgIncoming').value.trim()
    });
    toast('设置已保存'); NODE = await api('/api/info'); renderInfo();
    $('#nodeInfo').textContent = `${NODE.name} · 共享「${NODE.root}」· ${NODE.local_ips.join(' / ')}:${NODE.port}`;
  } catch (e) { toast('保存失败：' + e.message); }
};
$('#btnSaveKey').onclick = async () => {
  const v = $('#cfgKey').value.trim();
  if (!confirm('修改群组密钥后，本页需要重新输入新密钥才能继续操作。确定修改吗？')) return;
  try {
    const r = await jpost('/api/config', { key: v });
    if (r.config.key) { KEY = r.config.key; localStorage.setItem('ls_key', KEY); }
    toast('密钥已更新，正在重新载入…');
    setTimeout(() => location.reload(), 1200);
  } catch (e) { toast('更新失败：' + e.message); }
};
$('#btnAddDev').onclick = async () => {
  const host = $('#inHost').value.trim();
  if (!host) return toast('请输入 IP 地址');
  const btn = $('#btnAddDev'); btn.disabled = true;
  try {
    const r = await jpost('/api/devices', { host, key: $('#inKey').value.trim() || undefined });
    toast('已添加 ' + (r.device ? r.device.name : host) + (r.rtt !== undefined ? '（' + r.rtt + ' ms）' : ''));
    $('#inHost').value = ''; $('#inKey').value = '';
    await refreshDevices();
  } catch (e) { toast('添加失败：' + e.message, 4200); }
  finally { btn.disabled = false; }
};
$$('.tab').forEach(t => t.onclick = () => switchTab(t.dataset.tab));

/* 拖拽上传 */
let dragDepth = 0;
window.addEventListener('dragenter', e => { e.preventDefault(); if (!CUR.dev && ++dragDepth === 1) document.body.classList.add('drop'); });
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('dragleave', e => { e.preventDefault(); if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove('drop'); } });
window.addEventListener('drop', e => {
  e.preventDefault(); dragDepth = 0; document.body.classList.remove('drop');
  if (CUR.dev) { toast('远程目录不支持上传，请切换到本机'); return; }
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) uploadList(e.dataTransfer.files);
});

/* 初始化 */
(function () {
  if (KEY) document.cookie = 'ls_key=' + encodeURIComponent(KEY) + ';path=/;max-age=31536000;SameSite=Lax';
  const q = new URLSearchParams(location.search);
  if (q.get('key')) {
    KEY = q.get('key');
    localStorage.setItem('ls_key', KEY);
    document.cookie = 'ls_key=' + encodeURIComponent(KEY) + ';path=/;max-age=31536000;SameSite=Lax';
    history.replaceState(null, '', location.pathname);
  }
  boot();
  window.addEventListener('beforeunload', () => { });
})();
</script>
</body>
</html>
"""
