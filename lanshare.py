#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LanShare —— 局域网多设备文件互查 / 互传工具

功能
  * 文件浏览：网页浏览共享目录，隐藏文件开关、排序、新建文件夹、重命名、删除
  * 上传下载：拖拽/多选上传（手机电脑均可），浏览器原生下载，HTTP Range 断点续传
  * 设备发现：UDP 广播自动发现同一局域网内其它 LanShare 节点，也可手动填 IP 添加
  * 多节点互传：把文件或整个目录「推送」到其它设备，也可从其它设备「拉取」到本机
  * 断点续传：上传、下载、节点间互传均支持中断后从已传字节继续
  * 共享目录：默认只暴露启动时指定的目录，路径穿越一律拒绝
  * 手机自适应：内置响应式网页，手机浏览器直接可用

运行
  python lanshare.py                       # 共享当前目录，端口 8765
  python lanshare.py --root "D:\\共享"     # 指定共享目录
  python lanshare.py --port 9000 --name 客厅电脑 --open
  python lanshare.py --no-auth             # 关闭群组密钥（同一局域网内任何人可访问）

多设备协作：默认自动生成一个「群组密钥」。在第二台设备上打开第一台的网页地址，
输入同一个密钥即可（第二台会自动沿用该密钥），之后两台设备就能互相发现、互传文件。

零第三方依赖，Python 3.8+ 即可运行。
"""

__version__ = '1.5.0'
APP_NAME = 'LanShare'

import argparse
import base64
import codecs
import datetime
import fnmatch
import http.client
import http.server
import ipaddress
import json
import logging
import mimetypes
import os
import posixpath
import queue
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
import xml.sax.saxutils

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8765
DISCOVERY_PORT = 48899
MAGIC = 'LANSHARE/1'
CHUNK = 256 * 1024
REMOTE_CHUNK = 1024 * 1024
# 小文件批量打包阈值：单个文件不超过这个大小、且一批不超过这些上限时合并成一次请求
BATCH_MAX_FILE = 1024 * 1024
BATCH_MAX_BYTES = 8 * 1024 * 1024
BATCH_MAX_FILES = 128
HTTP_TIMEOUT = 60
CONFIG_DIRNAME = '.lanshare'
# WebDAV 挂载点：/dav/ 下就是共享目录，Windows 可用 net use 挂成网络驱动器
DAV_BASE = '/dav'

log = logging.getLogger('lanshare')

# 是否为「无控制台」的图形模式（打包成 exe 双击运行时为 True）
GUI_MODE = (os.name == 'nt' and
            (getattr(sys, 'frozen', False) or '--gui' in sys.argv))


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def now_ts():
    return time.time()


def setup_console_encoding():
    """
    让控制台输出与 Windows 控制台代码页保持一致。

    背景：如果系统里设了 PYTHONUTF8=1 或 PYTHONIOENCODING=utf-8，
    Python 会按 UTF-8 往控制台写，而中文版 cmd 窗口按 GBK(936) 显示，
    中文日志就会变成「鑷姩鍙戠幇」这类乱码。这里主动读取控制台真实代码页并对齐。
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
    except Exception:
        return
    if not cp:
        return
    codec = 'cp%d' % cp
    try:
        codecs.lookup(codec)
    except LookupError:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(stream, 'encoding', '') or '').lower().replace('-', '').replace('_', '')
            if enc and enc not in (codec, codec.replace('cp', '')):
                stream.reconfigure(encoding=codec, errors='replace')
        except Exception:
            pass


def human_size(n):
    try:
        n = float(n)
    except Exception:
        return '?'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.2f %s' % (n, unit))
        n /= 1024.0
    return '%.2f TB' % n


def local_ips():
    """本机所有 IPv4 地址（局域网地址优先）。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = item[4][0]
            if ip and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    ips = [ip for ip in ips if not ip.startswith('127.')]
    return ips or ['127.0.0.1']


def primary_ip():
    """本机首选局域网 IPv4 地址。"""
    return local_ips()[0]


def norm_rel(path):
    """规范成相对共享根的 posix 相对路径（无前导 /）。"""
    if path is None:
        path = ''
    path = str(path).replace('\\', '/').strip()
    path = posixpath.normpath('/' + path).lstrip('/')
    if path in ('.', '/'):
        path = ''
    return path


def safe_join(root, rel):
    """在 root 下安全拼接 rel；任何越界（路径穿越）直接拒绝。"""
    rel = norm_rel(rel)
    root_abs = os.path.abspath(root)
    full = os.path.abspath(os.path.join(root_abs, rel.replace('/', os.sep)))
    if full != root_abs and not full.startswith(root_abs + os.sep):
        raise ValueError('路径越界，已拒绝访问：%s' % rel)
    return full


# --- 全盘模式：虚拟根 = 「此电脑」，rel 的第一段是盘符（如 C:） ---------------

DRIVE_RE = re.compile(r'^[A-Za-z]:$')


def list_drives():
    """返回本机可用盘符列表，如 ['C:', 'D:']。"""
    if os.name != 'nt':
        return ['/']
    out = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        root = '%s:%s' % (letter, os.sep)
        try:
            if os.path.exists(root):
                out.append('%s:' % letter)
        except OSError:
            continue
    return out


def list_free_drives():
    """返回当前没被占用的盘符，如 ['Z:', 'Y:', ...]。"""
    if os.name != 'nt':
        return []
    try:
        used = {d[:1].upper() for d in list_drives()}
    except Exception:
        used = set()
    return ['%s:' % ch for ch in 'ZYXWVUTSRQPONMLKJIHGFED'
            if ch.upper() not in used]


def mounted_lanshare_drives():
    """列出已被 LanShare 挂载的驱动器（net use 查询）。"""
    out = []
    if os.name != 'nt':
        return out
    try:
        r = subprocess.run(['net', 'use'], capture_output=True, timeout=15,
                           creationflags=0x08000000)
    except Exception:
        return out
    raw = r.stdout or b''
    # net.exe 在中文 Windows 上是 GBK 输出，先按 OEM/本地编码解出来
    text = None
    for enc in ('gbk', 'cp936', 'utf-8'):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = raw.decode('utf-8', 'replace')
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('---'):
            continue
        low = line.lower()
        # net use 显示的是 UNC 形式：\\主机@端口\dav
        if '/dav' not in low and '\\dav' not in low:
            continue
        # net use 的输出前面有状态列（OK / 已断开），盘符不一定在第一列，
        # 所以扫一遍所有 token 找盘符。
        for tok in line.split():
            if len(tok) == 2 and tok[0].isalpha() and tok[1] == ':':
                out.append(tok.upper())
                break
    return out


def powershell_exe():
    """找一个可用的 PowerShell 可执行文件。"""
    windir = os.environ.get('SystemRoot') or r'C:\Windows'
    p = os.path.join(windir, 'System32', 'WindowsPowerShell', 'v1.0',
                     'powershell.exe')
    if os.path.isfile(p):
        return p
    return 'powershell.exe'


def is_admin():
    """当前进程是否已提权。"""
    if os.name != 'nt':
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def powershell_quote(text):
    """把字符串安全地嵌进 PowerShell 单引号字面量。"""
    return "'" + str(text).replace("'", "''") + "'"


def elevate_script(script_text, timeout=150):
    """以管理员身份运行一段 PowerShell 脚本（会弹 UAC）。

    脚本自己在末尾把结果写到临时文件，这里读回来。
    返回 (是否拿到结果, 结果文本)。
    """
    if os.name != 'nt':
        return False, '这个功能只在 Windows 上可用'
    tmpdir = tempfile.mkdtemp(prefix='lanshare-elev-')
    script = os.path.join(tmpdir, 'run.ps1')
    result = os.path.join(tmpdir, 'result.txt')
    body = ('$ErrorActionPreference = "Continue"\n'
            '$out = %s\n'
            '$ok = $false\n'
            'try {\n%s\n  $ok = $true\n} catch {\n'
            '  "ERROR: " + $_.Exception.Message | '
            'Set-Content -LiteralPath $out -Encoding UTF8\n}\n'
            'if ($ok) { if (-not (Test-Path $out)) { "OK" | '
            'Set-Content -LiteralPath $out -Encoding UTF8 } }\n'
            % (powershell_quote(result), script_text))
    with open(script, 'w', encoding='utf-8-sig') as f:
        f.write(body)

    ps = powershell_exe()
    args = [ps, '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-WindowStyle', 'Hidden', '-File', script]
    import ctypes
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', ps, subprocess.list2cmdline(args[1:]), None, 0)
    except Exception as exc:
        return False, '无法提权：%s' % exc
    if rc <= 32:
        return False, '已取消：挂载需要管理员权限'

    deadline = now_ts() + timeout
    while now_ts() < deadline:
        if os.path.isfile(result):
            time.sleep(0.3)
            try:
                with open(result, encoding='utf-8-sig') as f:
                    return True, f.read().strip()
            except Exception:
                return True, ''
        time.sleep(0.4)
    return False, '操作超时'


def mount_webdav(url, drive, username='LanShare', password='', fix_limits=True):
    """把 url 挂成 drive 盘符，返回 (成功, 说明)。会弹 UAC。"""
    if os.name != 'nt':
        return False, '这个功能只在 Windows 上可用'
    drive = str(drive).rstrip('\\/')
    if not re.match(r'^[A-Za-z]:$', drive):
        return False, '盘符不正确'
    drive = drive.upper()
    q = powershell_quote
    lines = []
    if fix_limits:
        # 默认 WebClient 限制：单文件 47.7MB、Basic 认证仅限 SSL，
        # 都会影响正常使用，这里一并放宽。
        lines += [
            '$p = "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\WebClient\\Parameters"',
            'Set-ItemProperty -Path $p -Name FileSizeLimitInBytes '
            '-Value 4294967295 -Type DWord -Force',
            'Set-ItemProperty -Path $p -Name BasicAuthLevel '
            '-Value 2 -Type DWord -Force',
            'Set-Service WebClient -StartupType Automatic',
            'Start-Service WebClient -ErrorAction SilentlyContinue',
        ]
    lines += [
        '& net.exe use %s /delete /y 2>$null | Out-Null' % drive,
        # 用参数数组传给 net.exe，避免引号/特殊字符被 cmd 解析坏掉
        '$a = @("use", %s, %s, %s, %s, "/persistent:no")'
        % (q(drive), q(url), q('/user:' + username), q(password)),
        '$r = (& net.exe @a 2>&1 | Out-String)',
        'Set-Content -LiteralPath $out -Value $r -Encoding UTF8',
        'if ($LASTEXITCODE -ne 0) { exit 1 }',
    ]
    ok, out = elevate_script('\n'.join(lines))
    if not ok:
        return False, out
    low = (out or '').lower()
    if 'successfully' in low or '成功' in (out or ''):
        return True, '已挂载到 %s' % drive
    return False, (out or '').strip() or '挂载失败，请确认对方在线'


def unmount_webdav(drive):
    """卸载盘符，返回 (成功, 说明)。"""
    if os.name != 'nt':
        return False, '这个功能只在 Windows 上可用'
    drive = str(drive).rstrip('\\/').upper()
    try:
        r = subprocess.run(['net', 'use', drive, '/delete', '/y'],
                           capture_output=True, text=True, timeout=30,
                           creationflags=0x08000000, errors='replace')
    except Exception as exc:
        return False, str(exc)
    return True, '已断开 %s' % drive


def resolve_whole(rel):
    """
    全盘模式下把相对路径解析成真实绝对路径。
      ''            -> 虚拟根，调用方应另行处理（列出盘符）
      'C:'          -> C:\\
      'C:/Users/x'  -> C:\\Users\\x
    """
    rel = norm_rel(rel)
    if not rel:
        raise ValueError('这是「此电脑」根，没有对应的物理路径')
    parts = rel.split('/')
    first = parts[0]
    if os.name == 'nt':
        if len(first) == 1 and first.isalpha():
            first = first.upper() + ':'
            parts[0] = first
        if not DRIVE_RE.match(first):
            raise ValueError('非法路径（需要以盘符开头，例如 C:/Users）：%s' % rel)
        drive = first.upper()
        full = os.path.abspath(os.path.join(drive + os.sep, *parts[1:]))
        # abspath 已折叠 ..，再确认没有跑到别的盘去
        if os.path.splitdrive(full)[0].upper() != drive:
            raise ValueError('路径越界，已拒绝访问：%s' % rel)
        return full
    # 非 Windows：整个根当作唯一「盘」
    return os.path.abspath(os.path.join(os.sep, *parts))


def join_rel(base, rel):
    base = norm_rel(base)
    rel = norm_rel(rel)
    return norm_rel(posixpath.join(base, rel) if base else rel)


def rel_parts(rel):
    return [p for p in norm_rel(rel).split('/') if p]


def basename_of(path):
    return os.path.basename(str(path).rstrip('\\/')) or str(path)


def is_hidden_name(name):
    return name.startswith('.') or name in ('$RECYCLE.BIN', 'System Volume Information')


def guess_content_type(name):
    ctype, _ = mimetypes.guess_type(name)
    return ctype or 'application/octet-stream'


def parse_size(text):
    """'100MB' / '1.5G' -> 字节数；空值返回 None。"""
    if text is None or text == '':
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = re.match(r'^\s*([\d.]+)\s*([kmgt]?b?)\s*$', str(text), re.I)
    if not m:
        return None
    mult = {'': 1, 'b': 1, 'k': 1024, 'kb': 1024, 'm': 1024 ** 2, 'mb': 1024 ** 2,
            'g': 1024 ** 3, 'gb': 1024 ** 3, 't': 1024 ** 4, 'tb': 1024 ** 4}
    return int(float(m.group(1)) * mult[(m.group(2) or '').lower()])


def new_id(n=12):
    return secrets.token_hex(max(1, n // 2))


def match_filter(rel, name, size, mtime, spec):
    """按筛选规则判断文件是否入选；spec 为空表示全部。"""
    if not spec:
        return True
    if spec.get('is_dir'):
        return True
    if spec.get('dirs_only'):
        return False
    exts = spec.get('exts') or []
    if isinstance(exts, str):
        exts = re.split(r'[,\s;]+', exts)
    if any(e.strip() for e in exts):
        dot = name.rfind('.')
        ext = name[dot + 1:].lower() if dot >= 0 else ''
        wanted = [str(e).strip().lower().lstrip('.') for e in exts if str(e).strip()]
        if ext not in wanted:
            return False
    globs = spec.get('globs') or []
    if isinstance(globs, str):
        globs = re.split(r'[,\n;]+', globs)
    globs = [str(g).strip() for g in globs if str(g).strip()]
    if globs:
        ok = False
        for pat in globs:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
                ok = True
                break
        if not ok:
            return False
    mn = parse_size(spec.get('min_size'))
    if mn is not None and size is not None and size < mn:
        return False
    mx = parse_size(spec.get('max_size'))
    if mx is not None and size is not None and size > mx:
        return False
    days = spec.get('newer_than_days')
    try:
        days = float(days) if days else None
    except Exception:
        days = None
    if days and mtime and mtime < now_ts() - days * 86400:
        return False
    return True


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class Config(object):
    # 配置格式版本。1 = 早期版本（默认只共享一个目录、默认生成群组密钥）
    #                   2 = 当前版本（默认全盘可访问、默认不设密钥）
    CONFIG_VERSION = 2
    FIELDS = ('name', 'port', 'root', 'key', 'https', 'allow_whole_disk',
              'discovery', 'auto_accept', 'incoming_dir', 'config_version',
              'download_dir')

    def __init__(self, script_dir):
        self.path = os.path.join(script_dir, 'lanshare.config.json')
        self.name = socket.gethostname() or 'LanShare 节点'
        self.port = DEFAULT_PORT
        self.root = ''
        self.key = ''
        self.https = False
        # 默认全盘可访问、不设密钥：这是自己家的局域网，怎么方便怎么来
        self.allow_whole_disk = True
        self.discovery = True
        self.auto_accept = True
        self.incoming_dir = 'LanShare 接收'
        # 下载保存位置：留空则每次弹窗选择；由 GUI 记住用户上次选的位置
        self.download_dir = ''
        self.config_version = self.CONFIG_VERSION
        self.load()
        # 旧版本配置（还没有 config_version 字段）自动升级为「全盘 + 免密钥」
        self._migrate()

    def _migrate(self):
        """把旧版本配置文件升级到当前默认：全盘可访问、免密钥。"""
        if self.config_version >= self.CONFIG_VERSION:
            return
        self.allow_whole_disk = True
        if self.key:
            log.info('已按新版默认设置关闭访问密钥（旧密钥：%s）', self.key)
            self.key = ''
        self.config_version = self.CONFIG_VERSION
        self.save()
        log.info('配置已升级：现在可以访问本机全部磁盘，且无需密钥')

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k in self.FIELDS:
                if k in data:
                    setattr(self, k, data[k])
            if 'config_version' not in data:
                self.config_version = 1      # 老配置文件
            log.debug('已载入配置 %s', self.path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning('读取配置失败（忽略，用默认值）：%s', exc)

    def save(self):
        data = self.to_dict()
        # 临时文件名带上进程号：同一目录下可能同时跑多个节点
        # （多开测试/多开程序），固定名字会互相抢文件。
        tmp = '%s.%d.tmp' % (self.path, os.getpid())
        last_exc = None
        for attempt in range(5):
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                # 目标文件可能正被另一个节点读着，重命名会失败，稍等重试
                for _ in range(4):
                    try:
                        os.replace(tmp, self.path)
                        return data
                    except OSError as exc:
                        last_exc = exc
                        time.sleep(0.05)
            except OSError as exc:
                last_exc = exc
            time.sleep(0.1 * (attempt + 1))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        log.warning('保存配置失败：%s', last_exc)
        return data

    def to_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    def default_incoming_rel(self):
        """接收目录的默认相对路径。全盘模式下必须带盘符，否则无法定位。"""
        if self.allow_whole_disk:
            drive = os.environ.get('SystemDrive', 'C:')
            return norm_rel('%s:/%s' % (drive.rstrip(':'), self.incoming_dir or 'LanShare 接收'))
        return norm_rel(self.incoming_dir or '')

    def dest_base(self, rel):
        """把用户给的接收目录规整成可用路径；为空时回落到默认接收目录。"""
        rel = norm_rel(rel)
        if rel:
            return rel
        return self.default_incoming_rel()


# ---------------------------------------------------------------------------
# 运行时状态
# ---------------------------------------------------------------------------

class State(object):
    def __init__(self, config, script_dir):
        self.config = config
        self.script_dir = script_dir
        self.data_dir = os.path.dirname(os.path.abspath(config.path)) or script_dir
        self.self_id = new_id()
        self.start_time = now_ts()
        self.devices = {}
        self.tasks = {}
        self.lock = threading.RLock()
        self.tasks_path = os.path.join(self.data_dir, 'lanshare.tasks.json')
        self.load_tasks()

    # ---- 设备 ----
    def upsert_device(self, info, ip=None, port=None, source='discovery'):
        dev_id = info.get('id')
        if not dev_id or dev_id == self.self_id:
            return None
        with self.lock:
            dev = self.devices.get(dev_id)
            is_new = dev is None
            if dev is None:
                dev = {'id': dev_id, 'first_seen': now_ts()}
                self.devices[dev_id] = dev
            dev.update({
                'name': info.get('name') or dev.get('name') or '未知设备',
                'port': int(port or info.get('port') or DEFAULT_PORT),
                'label': info.get('root') or dev.get('label') or '',
                'version': info.get('version') or dev.get('version') or '',
                'https': bool(info.get('https', dev.get('https', False))),
                'platform': info.get('platform') or dev.get('platform') or '',
                'whole_disk': bool(info.get('whole_disk', dev.get('whole_disk', False))),
                'last_seen': now_ts(),
                'source': source,
            })
            if ip:
                dev['host'] = ip
            if is_new:
                log.info('发现设备 %s @ %s:%s', dev.get('name'), dev.get('host'), dev.get('port'))
            return dict(dev)

    def devices_list(self, online_window=25):
        with self.lock:
            out = []
            for dev in self.devices.values():
                d = dict(dev)
                d['online'] = (now_ts() - d.get('last_seen', 0)) < online_window
                out.append(d)
            out.sort(key=lambda d: (not d['online'], d.get('name') or ''))
            return out

    def get_device(self, dev_id):
        with self.lock:
            dev = self.devices.get(dev_id)
            return dict(dev) if dev else None

    def prune_devices(self, max_age=3600):
        with self.lock:
            for k in [k for k, v in self.devices.items() if now_ts() - v.get('last_seen', 0) > max_age]:
                self.devices.pop(k, None)

    # ---- 任务 ----
    def new_task(self, kind, **kw):
        task = {
            'id': new_id(8), 'kind': kind, 'status': 'running',
            'created': now_ts(), 'updated': now_ts(), 'finished': None,
            'total_bytes': 0, 'sent_bytes': 0, 'total_files': 0, 'done_files': 0,
            'current': '', 'errors': [], 'cancel': False, 'pause': False,
            'speed': 0, 'progress': 0.0,
        }
        task.update(kw)
        with self.lock:
            self.tasks[task['id']] = task
        self.save_tasks()
        return task

    def get_task(self, task_id):
        with self.lock:
            return self.tasks.get(task_id)

    def find_receive_task(self, remote_id, path=''):
        with self.lock:
            for t in self.tasks.values():
                if t.get('kind') == 'receive' and t.get('remote_id') == remote_id \
                        and t.get('status') == 'running':
                    return t
        return None

    def tasks_list(self):
        with self.lock:
            items = [dict(t) for t in self.tasks.values()]
            items.sort(key=lambda t: -t.get('created', 0))
            return items[:200]

    def save_tasks(self):
        try:
            os.makedirs(os.path.dirname(self.tasks_path), exist_ok=True)
            with self.lock:
                data = [dict(t) for t in self.tasks.values()
                        if t.get('kind') in ('send', 'receive', 'pull')]
            with open(self.tasks_path, 'w', encoding='utf-8') as f:
                json.dump(data[-200:], f, ensure_ascii=False)
        except Exception:
            pass

    def save_tasks_soon(self, min_interval=1.0, force=False):
        """把任务落盘节流到最多每秒一次。

        传输时每个分片都会调到这里；大文件无所谓，但几百个小文件时
        每个文件都重写一遍任务文件会成为明显瓶颈。
        """
        now = now_ts()
        with self.lock:
            last = getattr(self, '_tasks_saved_at', 0.0)
            if not force and (now - last) < min_interval:
                self._tasks_dirty = True
                return
            self._tasks_saved_at = now
            self._tasks_dirty = False
        self.save_tasks()

    def load_tasks(self):
        try:
            with open(self.tasks_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for t in data:
                if t.get('status') == 'running':
                    t['status'] = 'error'
                    t['errors'] = (t.get('errors') or []) + ['程序重启，任务中断']
                t.setdefault('speed', 0)
                t.setdefault('progress', 0.0)
                t['cancel'] = False
                t['pause'] = False
                self.tasks[t['id']] = t
            if data:
                log.info('已恢复 %d 条历史传输记录', len(data))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 局域网自动发现（UDP 广播）
# ---------------------------------------------------------------------------

class Discovery(object):
    def __init__(self, state, interval=3.0, port=DISCOVERY_PORT):
        self.state = state
        self.interval = interval
        self.port = port
        self.stop_event = threading.Event()
        self.sock = None

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass
        try:
            sock.bind(('', self.port))
        except OSError as exc:
            log.warning('发现端口 %s 被占用（可能已运行另一个实例）：%s', self.port, exc)
            sock.close()
            return
        self.sock = sock
        threading.Thread(target=self._listen, name='ls-discovery-listen', daemon=True).start()
        threading.Thread(target=self._announce, name='ls-discovery-announce', daemon=True).start()
        log.info('自动发现已启动（UDP %s 广播）', self.port)

    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def payload(self):
        cfg = self.state.config
        return {
            'magic': MAGIC, 'id': self.state.self_id, 'name': cfg.name,
            'port': cfg.port,
            'root': '此电脑' if cfg.allow_whole_disk else basename_of(cfg.root),
            'https': cfg.https, 'whole_disk': cfg.allow_whole_disk,
            'version': __version__, 'platform': sys.platform, 'ts': now_ts(),
        }

    def _announce(self):
        data = json.dumps(self.payload(), ensure_ascii=False).encode('utf-8')
        while not self.stop_event.is_set():
            targets = [('255.255.255.255', self.port)]
            for ip in local_ips():
                parts = ip.split('.')
                if len(parts) == 4:
                    targets.append(('.'.join(parts[:3]) + '.255', self.port))
            for addr in targets:
                try:
                    self.sock.sendto(data, addr)
                except Exception:
                    pass
            self.stop_event.wait(self.interval)

    def _listen(self):
        self.sock.settimeout(1.0)
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode('utf-8', 'replace'))
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get('magic') == MAGIC and msg.get('id') != self.state.self_id:
                self.state.upsert_device(msg, ip=addr[0], port=msg.get('port'))


# ---------------------------------------------------------------------------
# 目录提供者
# ---------------------------------------------------------------------------

class PeerKeeper(object):
    """按 --peer 指定的地址定时探测并保活设备（用于自动发现不可用的网络）。"""

    def __init__(self, state, peers, interval=5.0):
        self.state = state
        self.peers = []
        for raw in peers:
            raw = str(raw).strip()
            if not raw:
                continue
            host, _, port = raw.partition(':')
            try:
                port = int(port or DEFAULT_PORT)
            except ValueError:
                port = DEFAULT_PORT
            if host:
                self.peers.append((host.strip(), port))
        self.interval = interval
        self.stop_event = threading.Event()

    def start(self):
        if not self.peers:
            return
        threading.Thread(target=self._loop, name='ls-peer-keeper', daemon=True).start()
        for host, port in self.peers:
            log.info('已配置固定对端：%s:%s（将定时探测）', host, port)

    def stop(self):
        self.stop_event.set()

    def _loop(self):
        while not self.stop_event.is_set():
            for host, port in self.peers:
                try:
                    probe = {'id': 'peer:%s:%s' % (host, port), 'name': host,
                             'host': host, 'port': port}
                    info = RemoteDirectory(probe, key=self.state.config.key, timeout=5).ping()
                    pong = info.get('info') or {}
                    self.state.upsert_device({
                        'id': pong.get('id'), 'name': pong.get('name') or host,
                        'port': port, 'version': pong.get('version'),
                        'platform': pong.get('platform'), 'label': pong.get('root') or '',
                        'whole_disk': pong.get('whole_disk', False),
                    }, ip=host, port=port, source='peer')
                except RemoteError as exc:
                    if exc.status == 401:
                        log.warning('对端 %s:%s 在线，但群组密钥不一致（请让两台设备使用同一个 --key）',
                                    host, port)
                    else:
                        log.debug('对端 %s:%s 暂不可达：%s', host, port, exc.message)
                except Exception as exc:
                    log.debug('对端 %s:%s 暂不可达：%s', host, port, exc)
            self.stop_event.wait(self.interval)


class RemoteError(Exception):
    def __init__(self, status, message, url=''):
        self.status = status
        self.message = message
        self.url = url
        super().__init__('HTTP %s: %s' % (status, message))


class LocalDirectory(object):
    """
    本机文件访问。

    whole_disk=True  ：全盘模式。虚拟根是「此电脑」，rel 形如 '' / 'C:' / 'C:/Users/xxx'。
    whole_disk=False ：共享目录模式。所有路径都限制在 root 之内。
    """

    is_remote = False

    def __init__(self, root, whole_disk=False):
        self.whole_disk = bool(whole_disk)
        self.root = os.path.abspath(root) if root else os.path.abspath(os.sep)
        self.name = '本机'

    def info(self):
        return {'name': self.name, 'is_remote': False, 'whole_disk': self.whole_disk}

    # ---- 路径解析 ----
    def _full(self, rel):
        if self.whole_disk:
            return resolve_whole(rel)
        return safe_join(self.root, rel)

    def _is_virtual_root(self, rel):
        return self.whole_disk and not norm_rel(rel)

    def list_drives_entries(self):
        """全盘模式下「此电脑」根，列出各磁盘分区。"""
        entries = []
        for drv in list_drives():
            try:
                du = shutil.disk_usage(drv + os.sep)
                total, free = du.total, du.free
                label = ''
                try:
                    import ctypes
                    buf = ctypes.create_unicode_buffer(261)
                    if ctypes.windll.kernel32.GetVolumeInformationW(
                            ctypes.c_wchar_p(drv + os.sep), buf, 261, None, None, None, None, 0):
                        label = buf.value
                except Exception:
                    label = ''
                entries.append({
                    'name': drv, 'is_dir': True, 'size': 0, 'mtime': 0,
                    'hidden': False, 'count': None, 'ext': '',
                    'drive': True, 'label': label, 'total': total, 'free': free,
                })
            except Exception:
                entries.append({
                    'name': drv, 'is_dir': True, 'size': 0, 'mtime': 0,
                    'hidden': False, 'count': None, 'ext': '', 'drive': True,
                    'label': '', 'total': None, 'free': None,
                })
        return entries

    def list_dir(self, rel, include_hidden=True):
        if self._is_virtual_root(rel):
            return self.list_drives_entries()
        full = self._full(rel)
        entries = []
        with os.scandir(full) as it:
            for entry in it:
                name = entry.name
                hidden = is_hidden_name(name)
                if hidden and not include_hidden:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                    count = None
                    if is_dir:
                        try:
                            count = len(os.listdir(entry.path))
                        except Exception:
                            count = None
                    entries.append({
                        'name': name, 'is_dir': is_dir,
                        'size': 0 if is_dir else st.st_size,
                        'mtime': st.st_mtime, 'hidden': hidden, 'count': count,
                        'ext': '' if (is_dir or '.' not in name) else name.rsplit('.', 1)[1].lower(),
                        'drive': False,
                    })
                except OSError:
                    continue
        entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
        return entries

    def stat_path(self, rel):
        if self._is_virtual_root(rel):
            return {'exists': True, 'is_dir': True, 'size': 0, 'mtime': 0}
        full = self._full(rel)
        if not os.path.exists(full):
            return {'exists': False}
        st = os.stat(full)
        return {'exists': True, 'is_dir': os.path.isdir(full),
                'size': st.st_size, 'mtime': st.st_mtime}

    def iter_bytes(self, rel, start=0, end=None, chunk=CHUNK, should_stop=None, on_progress=None):
        full = self._full(rel)
        with open(full, 'rb') as f:
            if start:
                f.seek(start)
            remaining = None if end is None else (end - start + 1)
            while True:
                if should_stop and should_stop():
                    return
                n = chunk if remaining is None else min(chunk, remaining)
                if n <= 0:
                    return
                data = f.read(n)
                if not data:
                    return
                if on_progress:
                    on_progress(len(data))
                yield data
                if remaining is not None:
                    remaining -= len(data)

    def mkdir(self, rel):
        if self._is_virtual_root(rel):
            raise ValueError('不能在「此电脑」根新建文件夹')
        os.makedirs(self._full(rel), exist_ok=True)

    def remove(self, rel):
        if self._is_virtual_root(rel):
            raise ValueError('不能删除「此电脑」根')
        if self.whole_disk and DRIVE_RE.match(norm_rel(rel) or ' '):
            raise ValueError('不能删除整个磁盘分区：%s' % rel)
        full = self._full(rel)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full)
        else:
            os.remove(full)

    def rename(self, rel, new_name):
        if self._is_virtual_root(rel):
            raise ValueError('不能重命名「此电脑」根')
        full = self._full(rel)
        new_name = os.path.basename(str(new_name).replace('\\', '/').rstrip('/'))
        if not new_name or new_name in ('.', '..'):
            raise ValueError('非法名称')
        target = os.path.join(os.path.dirname(full), new_name)
        if os.path.exists(target):
            raise ValueError('目标已存在：%s' % new_name)
        os.rename(full, target)
        return target

    def write_chunk(self, rel, offset, data, sender=None, total=0):
        if self._is_virtual_root(rel):
            raise ValueError('不能写入「此电脑」根')
        full = self._full(rel)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if offset == 0 or not os.path.exists(full):
            with open(full, 'wb') as f:
                f.write(data)
        else:
            with open(full, 'r+b') as f:
                f.seek(offset)
                f.write(data)
        return offset + len(data)

    def free_space(self):
        try:
            base = self.root if not self.whole_disk else (list_drives() or ['C:'])[0] + os.sep
            return shutil.disk_usage(base).free
        except Exception:
            return None


class RemoteDirectory(object):
    """通过 HTTP API 访问另一端节点的共享目录。"""

    is_remote = True

    def __init__(self, device, key='', timeout=HTTP_TIMEOUT):
        self.device = device
        self.name = device.get('name') or '远程设备'
        self.host = device.get('host')
        self.port = int(device.get('port') or DEFAULT_PORT)
        self.tls = bool(device.get('https'))
        self.key = key or ''
        self.timeout = timeout
        # 复用的长连接：大量小文件时，省掉每个请求一次的 TCP 握手
        self._sock = None
        self._sock_timeout = None
        # 下载流单独一条，和上面的请求连接池分开，避免互相占用
        self._stream_sock = None
        self._lock = threading.Lock()

    # ---- 低层 ----
    def _new_conn(self, timeout=None):
        timeout = timeout or self.timeout
        if self.tls:
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout,
                                               context=ssl._create_unverified_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def _conn(self, timeout=None):
        """取一个可复用的连接；超时参数变化或连接已失效时自动重连。"""
        want = timeout or self.timeout
        if self._sock is not None and self._sock_timeout != want:
            self.close()
        if self._sock is None:
            self._sock = self._new_conn(want)
            self._sock_timeout = want
        return self._sock

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._sock_timeout = None
        if self._stream_sock is not None:
            try:
                self._stream_sock.close()
            except Exception:
                pass
            self._stream_sock = None

    def _request(self, method, url, body=None, headers=None, timeout=None):
        """发送请求并返回 response；连接断了就重连一次。"""
        last_exc = None
        for attempt in (0, 1):
            conn = self._conn(timeout)
            try:
                conn.request(method, url, body=body, headers=headers or {})
                return conn.getresponse()
            except (http.client.HTTPException, OSError, socket.timeout) as exc:
                last_exc = exc
                self.close()
                if attempt:
                    raise
        raise last_exc if last_exc else RuntimeError('请求失败')

    def _finish(self, resp, raw):
        """读完响应体后，按需丢弃这条复用连接（保持连接池干净）。"""
        if resp.status >= 400 or resp.getheader('Connection', '').lower() == 'close':
            self.close()
        return raw

    def _headers(self, extra=None):
        h = {'X-LanShare-Key': self.key, 'User-Agent': 'LanShare/%s' % __version__}
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _err_text(raw):
        try:
            data = json.loads(raw.decode('utf-8', 'replace'))
            return data.get('error') or str(data)[:300]
        except Exception:
            return raw.decode('utf-8', 'replace')[:300].strip() or '未知错误'

    def _json(self, method, path, query=None, body=None, timeout=None, extra_headers=None):
        url = path + ('?' + urllib.parse.urlencode(query) if query else '')
        headers = self._headers(extra_headers)
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json; charset=utf-8'
        headers['Content-Length'] = str(len(payload) if payload is not None else 0)
        with self._lock:
            resp = self._request(method, url, body=payload, headers=headers, timeout=timeout)
            raw = resp.read()
            status = resp.status
            self._finish(resp, raw)
        if status >= 400:
            raise RemoteError(status, self._err_text(raw), url)
        return json.loads(raw.decode('utf-8', 'replace')) if raw else {}

    # ---- 与 LocalDirectory 对齐 ----
    @property
    def whole_disk(self):
        return bool(self.device.get('whole_disk'))

    def info(self):
        return {'name': self.name, 'is_remote': True, 'host': self.host, 'port': self.port,
                'whole_disk': self.whole_disk}

    def list_dir(self, rel, include_hidden=False):
        data = self._json('GET', '/api/fs/list',
                          {'path': norm_rel(rel), 'hidden': '1' if include_hidden else '0'})
        return data.get('entries', [])

    def stat_path(self, rel):
        return self._json('GET', '/api/fs/stat', {'path': norm_rel(rel)})

    def check_files(self, rel, files):
        return self._json('POST', '/api/fs/exists', body={
            'path': norm_rel(rel),
            'files': [{'rel': f['rel'], 'size': f.get('size', 0)} for f in files]})

    def iter_bytes(self, rel, start=0, end=None, chunk=REMOTE_CHUNK, should_stop=None, on_progress=None):
        # 下载流独占一条连接直到读完，与请求连接池分开；下个文件继续复用这条，
        # 省掉每个小文件一次 TCP 握手。
        conn = self._stream_sock
        try:
            if conn is None:
                conn = self._new_conn()
                self._stream_sock = conn
            headers = self._headers()
            if start or end is not None:
                headers['Range'] = 'bytes=%d-%s' % (start, '' if end is None else str(end))
            url = '/api/fs/download?' + urllib.parse.urlencode({'path': norm_rel(rel)})
            try:
                conn.request('GET', url, headers=headers)
                resp = conn.getresponse()
            except (http.client.HTTPException, OSError, socket.timeout):
                # 长连接被服务端关掉了，重连一次再试
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._new_conn()
                self._stream_sock = conn
                conn.request('GET', url, headers=headers)
                resp = conn.getresponse()
            if resp.status >= 400:
                body = resp.read()
                self.close_stream()
                raise RemoteError(resp.status, self._err_text(body), url)
            while True:
                if should_stop and should_stop():
                    # 中途停下时响应体没读完，这条连接已不可复用，直接丢弃
                    self.close_stream()
                    return
                data = resp.read(chunk)
                if not data:
                    return
                if on_progress:
                    on_progress(len(data))
                yield data
        except (http.client.HTTPException, OSError, socket.timeout):
            self.close_stream()
            raise

    def close_stream(self):
        if self._stream_sock is not None:
            try:
                self._stream_sock.close()
            except Exception:
                pass
            self._stream_sock = None

    def read_batch(self, rels):
        """一次取回多个小文件，返回 {rel: bytes}（失败的不在结果里）。"""
        import struct

        if not rels:
            return {}
        body = json.dumps({'paths': [norm_rel(r) for r in rels]},
                          ensure_ascii=False).encode('utf-8')
        headers = self._headers({
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': str(len(body)),
        })
        url = '/api/fs/read_batch'
        with self._lock:
            resp = self._request('POST', url, body=body, headers=headers, timeout=300)
            if resp.status >= 400:
                raw = resp.read()
                self._finish(resp, raw)
                raise RemoteError(resp.status, self._err_text(raw), url)
            total = int(resp.getheader('Content-Length') or 0)
            buf = resp.read(total) if total > 0 else resp.read()
            self._finish(resp, buf)
        if len(buf) < 4:
            raise RemoteError(502, '批量读取响应不完整', url)
        head_len = struct.unpack('<I', buf[:4])[0]
        head = json.loads(buf[4:4 + head_len].decode('utf-8'))
        offset = 4 + head_len
        out = {}
        for meta in head.get('files', []):
            if not meta.get('ok'):
                continue
            size = int(meta.get('size') or 0)
            out[norm_rel(meta.get('path'))] = buf[offset:offset + size]
            offset += size
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def mkdir(self, rel):
        return self._json('POST', '/api/fs/mkdir', body={'path': norm_rel(rel)})

    def remove(self, rel):
        return self._json('POST', '/api/fs/delete', body={'path': norm_rel(rel)})

    def write_chunk(self, rel, offset, data, sender=None, total=0):
        query = urllib.parse.urlencode({'path': norm_rel(rel), 'offset': int(offset)})
        headers = self._headers({
            'Content-Type': 'application/octet-stream',
            'Content-Length': str(len(data)),
            'X-LanShare-From': (sender or {}).get('id', ''),
            'X-LanShare-From-Name': urllib.parse.quote((sender or {}).get('name', '')),
            'X-LanShare-Total': str(int(total or 0)),
        })
        url = '/api/fs/write?' + query
        with self._lock:
            resp = self._request('PUT', url, body=data, headers=headers)
            raw = resp.read()
            status = resp.status
            self._finish(resp, raw)
        if status >= 400:
            raise RemoteError(status, self._err_text(raw), '/api/fs/write')
        return json.loads(raw.decode('utf-8', 'replace')).get('written', offset + len(data))

    def write_batch(self, items, sender=None, total_bytes=0, total_files=0):
        """一次请求写入多个小文件。

        items: [{'rel': 目标相对路径, 'offset': 起始偏移, 'total': 该文件总大小, 'data': bytes}]
        """
        import struct

        head = {
            'total_bytes': int(total_bytes),
            'total_files': int(total_files or len(items)),
            'files': [{'path': norm_rel(it['rel']), 'size': len(it.get('data') or b''),
                       'offset': int(it.get('offset') or 0),
                       'total': int(it.get('total') or 0)} for it in items],
        }
        head_raw = json.dumps(head, ensure_ascii=False).encode('utf-8')
        body = bytearray(struct.pack('<I', len(head_raw)))
        body += head_raw
        for it in items:
            body += it.get('data') or b''
        body = bytes(body)
        headers = self._headers({
            'Content-Type': 'application/octet-stream',
            'Content-Length': str(len(body)),
            'X-LanShare-From': (sender or {}).get('id', ''),
            'X-LanShare-From-Name': urllib.parse.quote((sender or {}).get('name', '')),
        })
        url = '/api/fs/write_batch'
        with self._lock:
            resp = self._request('PUT', url, body=body, headers=headers, timeout=300)
            raw = resp.read()
            status = resp.status
            self._finish(resp, raw)
        if status >= 400:
            raise RemoteError(status, self._err_text(raw), url)
        return json.loads(raw.decode('utf-8', 'replace'))

    def announce_incoming(self, sender, files, dest_path, overwrite='skip'):
        return self._json('POST', '/api/receive', body={
            'from': sender.get('id'), 'from_name': sender.get('name'),
            'from_host': sender.get('host') or '',
            'from_port': int(sender.get('port') or self.port),
            'from_https': bool(sender.get('https')),
            'path': norm_rel(dest_path), 'overwrite': overwrite,
            'files': files, 'total': sum(int(f.get('size') or 0) for f in files)})

    def ping(self):
        t0 = now_ts()
        data = self._json('GET', '/api/ping', timeout=4)
        return {'ok': True, 'rtt': int((now_ts() - t0) * 1000), 'info': data}


# ---------------------------------------------------------------------------
# 传输引擎
# ---------------------------------------------------------------------------

def list_tree(provider, rel, filter_spec=None, should_stop=None):
    out = []
    for e in provider.list_dir(rel, include_hidden=False):
        if should_stop and should_stop():
            return out
        child = join_rel(rel, e['name'])
        if e['is_dir']:
            out.append({'rel': child, 'is_dir': True, 'size': 0,
                        'mtime': e.get('mtime', 0), 'name': e['name']})
            out.extend(list_tree(provider, child, filter_spec, should_stop))
        else:
            spec = dict(filter_spec) if filter_spec else {}
            spec['is_dir'] = False
            if match_filter(child, e['name'], e.get('size', 0), e.get('mtime', 0), spec):
                out.append({'rel': child, 'is_dir': False, 'size': e.get('size', 0),
                            'mtime': e.get('mtime', 0), 'name': e['name']})
    return out


def provider_is_whole_disk(provider):
    """判断这个 provider 的根是不是「此电脑」虚拟根。"""
    dev = getattr(provider, 'device', None)
    if isinstance(dev, dict) and dev.get('whole_disk'):
        return True
    return bool(getattr(provider, 'whole_disk', False))


def build_payload(provider, base_rel, filter_spec=None, should_stop=None):
    """整理待传内容：目录列表 + 文件列表（含大小、时间）。"""
    base_rel = norm_rel(base_rel)
    if not base_rel and provider_is_whole_disk(provider):
        # 「此电脑」是虚拟根，没有对应实体目录，必须选到具体磁盘
        raise ValueError('这是「此电脑」根，请先进入某个磁盘或文件夹再传输')
    st = provider.stat_path(base_rel)
    if not st.get('exists'):
        raise ValueError('源路径不存在：%s' % (base_rel or '/'))
    if base_rel:
        base_name = posixpath.basename(base_rel)
    elif provider.is_remote:
        base_name = '远程目录'
    else:
        base_name = basename_of(provider.root)
    if st.get('is_dir'):
        nodes = list_tree(provider, base_rel, filter_spec, should_stop)
        return {'base_rel': base_rel, 'base_name': base_name,
                'dirs': [{'rel': n['rel']} for n in nodes if n['is_dir']],
                'files': [{'rel': n['rel'], 'size': n['size'], 'mtime': n['mtime']}
                          for n in nodes if not n['is_dir']]}
    spec = dict(filter_spec) if filter_spec else {}
    spec['is_dir'] = False
    if not match_filter(base_rel, posixpath.basename(base_rel), st.get('size', 0),
                        st.get('mtime', 0), spec):
        return {'base_rel': base_rel, 'base_name': base_name, 'dirs': [], 'files': []}
    return {'base_rel': base_rel, 'base_name': base_name, 'dirs': [],
            'files': [{'rel': base_rel, 'size': st.get('size', 0), 'mtime': st.get('mtime', 0)}]}


def run_transfer(src, dst, payload, task, dest_base='', sender=None, on_file_start=None,
                 overwrite='skip'):
    """
    把 src 上 payload 描述的内容传到 dst。
      payload['base_rel'] 源根（文件或目录）
      dest_base           目标端目录（相对其共享根）
      overwrite           'overwrite' 时不做跳过判断，其余值会跳过同名同大小的文件
    返回 (ok, skipped, errors, moved_bytes)
    """
    base_rel = payload['base_rel']
    base_name = payload['base_name'] or 'LanShare'
    files = payload['files']
    dirs = payload['dirs']
    single = (not dirs) and len(files) == 1 and files[0]['rel'] == base_rel and bool(base_rel)
    # flat：files/dirs 里的 rel 已是「相对源共享根」的完整路径，直接平铺到 dest_base 下
    flat = bool(payload.get('flat'))
    if flat:
        dest_root = norm_rel(dest_base)
    elif single:
        dest_root = norm_rel(dest_base)
    else:
        dest_root = join_rel(dest_base, base_name)

    if flat:
        if dest_root:
            dst.mkdir(dest_root)
        for d in dirs:
            dst.mkdir(join_rel(dest_root, d['rel']))
    elif not single:
        dst.mkdir(dest_root)
        for d in dirs:
            child = d['rel'][len(base_rel):].lstrip('/') if base_rel and d['rel'].startswith(base_rel) else d['rel']
            dst.mkdir(join_rel(dest_root, child))
    else:
        if dest_root:
            dst.mkdir(dest_root)

    ok = skipped = moved = 0
    errors = []
    total = task.get('total_bytes') or 0

    def target_of(rel):
        if flat:
            return join_rel(dest_root, rel)
        if single:
            return join_rel(dest_root, posixpath.basename(rel))
        child = rel[len(base_rel):].lstrip('/') if base_rel and rel.startswith(base_rel) else rel
        return join_rel(dest_root, child)

    # 目标端是否已有同名文件：一次性批量问清（每个文件一次查询在零散文件时会很慢）
    known = None
    if overwrite != 'overwrite' and files:
        probe = [{'rel': target_of(f['rel']), 'size': f.get('size', 0)} for f in files]
        try:
            res = dst.check_files('', probe)
            known = {r['target']: r for r in res.get('results', []) if r.get('target')}
        except Exception:
            known = None

    # 小文件批量打包：目标端支持时，把多个小文件合并成一次请求，
    # 避免每个文件一次往返导致「零散文件特别慢」。
    # 只有远程目标才需要批量（本机内部复制没有网络往返开销）
    batch_limit = BATCH_MAX_FILES if getattr(dst, 'is_remote', False) else 0
    use_batch = batch_limit > 1 and callable(getattr(dst, 'write_batch', None))
    batch = []
    batch_bytes = 0
    # 远程源：一次性取回一批小文件，省掉每个文件一次下载往返
    use_read_batch = callable(getattr(src, 'read_batch', None))
    read_cache = {}
    small_files = [f for f in files
                   if 0 < int(f.get('size') or 0) <= BATCH_MAX_FILE] if use_read_batch else []
    small_idx = {}

    def prefetch_small(start_index):
        """把从 start_index 起的一批小文件一次读回来，填进 read_cache。"""
        if not use_read_batch or not small_files:
            return
        picked = []
        acc = 0
        for f in small_files:
            rel = f['rel']
            if rel in read_cache or rel in small_idx:
                continue
            size = int(f.get('size') or 0)
            if picked and (len(picked) >= BATCH_MAX_FILES or acc + size > BATCH_MAX_BYTES):
                break
            picked.append(rel)
            acc += size
        if not picked:
            return
        for rel in picked:
            small_idx[rel] = True
        try:
            read_cache.update(src.read_batch(picked))
        except Exception as exc:
            log.warning('批量读取失败：%s', exc)

    def flush_batch():
        nonlocal ok, moved, batch_bytes
        if not batch:
            return
        try:
            res = dst.write_batch(list(batch), sender=sender,
                                  total_bytes=total, total_files=task.get('total_files') or 0)
            for r in res.get('results', []):
                if not r.get('ok'):
                    errors.append('%s：%s' % (r.get('path'), r.get('error') or '写入失败'))
            ok += sum(1 for r in res.get('results', []) if r.get('ok'))
            task['done_files'] = task.get('done_files', 0) + sum(
                1 for r in res.get('results', []) if r.get('ok'))
            task['sent_bytes'] = task.get('sent_bytes', 0) + res.get('written', batch_bytes)
            moved += res.get('written', batch_bytes)
        except Exception as exc:
            errors.append('批量写入失败：%s' % exc)
            log.warning('批量写入失败：%s', exc)
        finally:
            batch.clear()
            batch_bytes = 0
        task['updated'] = now_ts()
        if total:
            task['progress'] = min(100.0, task.get('sent_bytes', 0) * 100.0 / total)

    for finfo in files:
        if task.get('cancel'):
            break
        while task.get('pause') and not task.get('cancel'):
            time.sleep(0.3)
        if task.get('cancel'):
            break

        rel = finfo['rel']
        size = int(finfo.get('size') or 0)
        target_rel = target_of(rel)

        task['current'] = rel
        task['updated'] = now_ts()
        if on_file_start:
            on_file_start(rel, target_rel, size)

        start = 0
        st = None
        if known is not None:
            # 调用方已经批量查过目标端，直接用结果，省掉每个文件一次往返
            hit = known.get(target_rel)
            if hit is not None:
                st = {'exists': bool(hit.get('exists')),
                      'is_dir': bool(hit.get('is_dir')),
                      'size': hit.get('size', 0)}
        if st is None:
            try:
                st = dst.stat_path(target_rel)
            except Exception:
                st = {'exists': False}
        if st.get('exists') and not st.get('is_dir'):
            have = int(st.get('size') or 0)
            if have == size:
                skipped += 1
                task['done_files'] = task.get('done_files', 0) + 1
                task['sent_bytes'] = task.get('sent_bytes', 0) + size
                moved += size
                task['updated'] = now_ts()
                continue
            if 0 < have < size:
                start = have
                task['sent_bytes'] = task.get('sent_bytes', 0) + have
                moved += have

        try:
            if size == 0:
                if use_batch:
                    batch.append({'rel': target_rel, 'offset': 0, 'total': 0, 'data': b''})
                    if len(batch) >= batch_limit:
                        flush_batch()
                else:
                    dst.write_chunk(target_rel, 0, b'', sender=sender, total=0)
                    ok += 1
                    task['done_files'] = task.get('done_files', 0) + 1
                continue

            # 小文件（不需要续传）：远程源先按批读回，再按批写过去，
            # 把「每个文件一次往返」压成「每批一次往返」。
            if start == 0 and 0 < size <= BATCH_MAX_FILE and (use_batch or use_read_batch):
                payload_bytes = read_cache.pop(rel, None)
                if payload_bytes is None and use_read_batch:
                    prefetch_small(0)          # 这一批小文件一起取回来
                    payload_bytes = read_cache.pop(rel, None)
                if payload_bytes is None:
                    payload_bytes = b''.join(
                        src.iter_bytes(rel, start=0, should_stop=lambda: task.get('cancel')))
                if task.get('cancel'):
                    break
                if len(payload_bytes) != size:
                    errors.append('%s：读取不完整（%d/%d 字节）'
                                  % (rel, len(payload_bytes), size))
                    continue
                if use_batch:
                    batch.append({'rel': target_rel, 'offset': 0, 'total': size,
                                  'data': payload_bytes})
                    batch_bytes += len(payload_bytes)
                    if len(batch) >= batch_limit or batch_bytes >= BATCH_MAX_BYTES:
                        flush_batch()
                else:
                    # 目标是本机：直接落盘，无需网络往返
                    dst.write_chunk(target_rel, 0, payload_bytes, sender=sender, total=size)
                    ok += 1
                    task['done_files'] = task.get('done_files', 0) + 1
                    task['sent_bytes'] = task.get('sent_bytes', 0) + size
                    moved += size
                    task['updated'] = now_ts()
                    if total:
                        task['progress'] = min(100.0,
                                               task.get('sent_bytes', 0) * 100.0 / total)
                continue

            acc = start
            t0 = now_ts()
            speed_bytes = 0
            for chunk in src.iter_bytes(rel, start=start, should_stop=lambda: task.get('cancel')):
                if task.get('cancel'):
                    break
                while task.get('pause') and not task.get('cancel'):
                    time.sleep(0.3)
                dst.write_chunk(target_rel, acc, chunk, sender=sender, total=size)
                acc += len(chunk)
                moved += len(chunk)
                task['sent_bytes'] = task.get('sent_bytes', 0) + len(chunk)
                task['updated'] = now_ts()
                speed_bytes += len(chunk)
                elapsed = now_ts() - t0
                if elapsed >= 1.0:
                    task['speed'] = speed_bytes / elapsed
                    speed_bytes = 0
                    t0 = now_ts()
                if total:
                    task['progress'] = min(100.0, task['sent_bytes'] * 100.0 / total)
            task['speed'] = 0
            if task.get('cancel'):
                break
            if acc < size:
                errors.append('%s：传输不完整（%d/%d 字节）' % (rel, acc, size))
                continue
            ok += 1
            task['done_files'] = task.get('done_files', 0) + 1
            if total:
                task['progress'] = min(100.0, task['sent_bytes'] * 100.0 / total)
        except Exception as exc:
            errors.append('%s：%s' % (rel, exc))
            log.warning('传输失败 %s -> %s：%s', rel, target_rel, exc)

    flush_batch()
    return ok, skipped, errors, moved


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = '%s/%s' % (APP_NAME, __version__)
    protocol_version = 'HTTP/1.1'
    timeout = 300
    disable_nagle_algorithm = True
    state = None
    # WebDAV 锁（简单记录，够 Windows 资源管理器用）
    _dav_locks = {}

    # ---- 基础 ----
    def log_message(self, fmt, *args):
        log.debug('%s - %s', self.address_string(), fmt % args)

    def _query(self):
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _cookies(self):
        out = {}
        for part in (self.headers.get('Cookie') or '').split(';'):
            if '=' in part:
                k, v = part.split('=', 1)
                out[k.strip()] = v.strip()
        return out

    def _send_json(self, obj, status=200, close=False):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-LanShare-Key')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        if close:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _ok(self, **kw):
        data = {'ok': True}
        data.update(kw)
        self._send_json(data)

    def _err(self, message, status=400):
        self._send_json({'ok': False, 'error': str(message)}, status=status, close=True)

    def _read_exact(self, n, chunk=64 * 1024):
        buf = bytearray()
        remaining = n
        while remaining > 0:
            data = self.rfile.read(min(chunk, remaining))
            if not data:
                break
            buf.extend(data)
            remaining -= len(data)
        return bytes(buf)

    def _read_json(self, limit=16 * 1024 * 1024):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return {}
        if length > limit:
            raise ValueError('请求体过大')
        raw = self._read_exact(length)
        return json.loads(raw.decode('utf-8', 'replace')) if raw else {}

    # ---- 鉴权 ----
    @staticmethod
    def _decode_key(raw):
        """
        还原客户端发来的密钥。

        HTTP 头只能是 ISO-8859-1，浏览器无法把中文等字符直接放进头部
        （会抛 “String contains non ISO-8859-1 code point”）。
        因此前端对非 ASCII 密钥做了 base64 编码并加 'b64:' 前缀，
        这里解回来。ASCII 密钥保持原样，兼容旧客户端。
        """
        if not raw:
            return ''
        if raw.startswith('b64:'):
            try:
                return base64.b64decode(raw[4:]).decode('utf-8')
            except Exception:
                return raw
        return raw

    def _given_key(self, query):
        raw = self.headers.get('X-LanShare-Key') or query.get('key') or ''
        if not raw:
            # cookie 是前端用 encodeURIComponent 写的，要先还原（b64: 里的冒号会被编码成 %3A）
            raw = urllib.parse.unquote(self._cookies().get('ls_key') or '')
        return self._decode_key(raw)

    def _authed(self, query):
        key = self.state.config.key
        if not key:
            return True
        given = self._given_key(query)
        # 注意：secrets.compare_digest 对含非 ASCII 的 str 会抛 TypeError
        # （"comparing strings with non-ASCII characters is not supported"），
        # 所以先编码成 bytes 比较，这样中文密码也能安全工作。
        try:
            return secrets.compare_digest(given.encode('utf-8'), key.encode('utf-8'))
        except Exception:
            return False

    # ---- 分发 ----
    def do_OPTIONS(self):
        if self._dav_rel() is not None:
            return self._dav_options()
        self._read_json() if (self.headers.get('Content-Length') or '0') != '0' else None
        self._send_json({'ok': True})

    # WebDAV：把共享目录按标准 WebDAV 暴露在 /dav/ 下，
    # 这样 Windows 可以用 net use 把远端挂成"我的电脑"里的网络驱动器。
    def do_PROPFIND(self):
        self._dav_propfind()

    def do_PROPPATCH(self):
        self._dav_proppatch()

    def do_MKCOL(self):
        self._dav_mkcol()

    def do_MOVE(self):
        self._dav_move(copy=False)

    def do_COPY(self):
        self._dav_move(copy=True)

    def do_LOCK(self):
        self._dav_lock()

    def do_UNLOCK(self):
        self._dav_unlock()

    def do_GET(self):
        if self._dav_rel() is not None:
            return self._dav_get(head=False)
        self._handle('GET')

    def do_HEAD(self):
        if self._dav_rel() is not None:
            return self._dav_get(head=True)
        self._handle('HEAD')

    def do_POST(self):
        self._handle('POST')

    def do_PUT(self):
        if self._dav_rel() is not None:
            return self._dav_put()
        self._handle('PUT')

    def do_DELETE(self):
        if self._dav_rel() is not None:
            return self._dav_delete()
        self._handle('DELETE')

    # ---- WebDAV ----
    def _dav_rel(self):
        """请求是否落在 /dav/ 下；是则返回去掉前缀后的相对路径（posix，无前导斜杠）。"""
        p = urllib.parse.urlparse(self.path).path
        if p == DAV_BASE:
            return ''
        if p.startswith(DAV_BASE + '/'):
            return norm_rel(urllib.parse.unquote(p[len(DAV_BASE):]))
        return None

    def _dav_local(self):
        cfg = self.state.config
        return LocalDirectory(cfg.root, whole_disk=cfg.allow_whole_disk)

    def _dav_internal(self, rel):
        """DAV 路径 -> 内部相对路径。

        全盘模式下盘符在 DAV 里不能带冒号（冒号是 Windows 文件名的非法字符，
        带上去资源管理器会打不开），所以对外用 'C'，内部再还原成 'C:'。
        """
        rel = norm_rel(rel)
        local = self._dav_local()
        if not rel or not local.whole_disk:
            return rel
        parts = rel.split('/')
        first = parts[0]
        if len(first) == 1 and first.isalpha():
            parts[0] = first.upper() + ':'
        elif DRIVE_RE.match(first):
            parts[0] = first.upper()
        return '/'.join(parts)

    def _dav_public(self, rel):
        """内部相对路径 -> DAV 路径（去掉盘符冒号）。"""
        rel = norm_rel(rel)
        local = self._dav_local()
        if not rel or not local.whole_disk:
            return rel
        parts = rel.split('/')
        if DRIVE_RE.match(parts[0]):
            parts[0] = parts[0][0].upper()
        return '/'.join(parts)

    def _dav_full(self, rel):
        return self._dav_local()._full(self._dav_internal(rel))

    def _dav_basic_ok(self):
        """WebDAV 客户端没有自定义请求头，鉴权只能走 HTTP Basic。

        只要填了群组密钥，用户名随便是谁都行，密码必须是密钥。
        """
        key = self.state.config.key
        if not key:
            return True
        auth = self.headers.get('Authorization') or ''
        if not auth.startswith('Basic '):
            return False
        try:
            raw = base64.b64decode(auth[6:].strip() + '===').decode('utf-8', 'replace')
        except Exception:
            return False
        _user, sep, pwd = raw.partition(':')
        if not sep:
            return False
        try:
            return secrets.compare_digest(pwd.encode('utf-8'), key.encode('utf-8'))
        except Exception:
            return False

    def _dav_auth(self):
        """通过返回 True；否则回 401 并提示 Basic 认证，返回 False。"""
        if self._dav_basic_ok():
            return True
        try:
            body = '需要 LanShare 群组密钥'
            data = body.encode('utf-8')
            self.send_response(401)
            self.send_header('WWW-Authenticate',
                             'Basic realm="LanShare %s"' % self.state.config.name)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        return False

    def _dav_xml(self, body, status=207):
        data = ('<?xml version="1.0" encoding="utf-8"?>' + body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/xml; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dav_status(self, status, body=b''):
        self.send_response(status)
        if body:
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if body and self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def _dav_href(self, rel, is_dir):
        rel = norm_rel(rel)
        quoted = urllib.parse.quote(rel)
        href = DAV_BASE + '/' + quoted if rel else DAV_BASE + '/'
        if is_dir and not href.endswith('/'):
            href += '/'
        return href

    def _dav_entry_xml(self, full, rel, display=None):
        # full 可能是 None：全盘模式的「此电脑」虚拟根没有实体路径
        is_dir = True if full is None else os.path.isdir(full)
        if full is None:
            size, mtime = 0, self.date_time_string()
        else:
            try:
                st = os.stat(full)
                size = 0 if is_dir else st.st_size
                mtime = self.date_time_string(st.st_mtime)
            except OSError:
                size, mtime = 0, self.date_time_string()
        name = display if display is not None else (os.path.basename(full) or 'LanShare')
        href = self._dav_href(rel, is_dir)
        esc = xml.sax.saxutils.escape
        return (
            '<D:response><D:href>%s</D:href>'
            '<D:propstat><D:prop>'
            '<D:displayname>%s</D:displayname>'
            '<D:getcontentlength>%d</D:getcontentlength>'
            '<D:getlastmodified>%s</D:getlastmodified>'
            '<D:resourcetype>%s</D:resourcetype>'
            '<D:iscollection>%d</D:iscollection>'
            '<D:ishidden>0</D:ishidden>'
            '<D:getcontenttype>%s</D:getcontenttype>'
            '</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>'
            '</D:response>' % (
                esc(href), esc(name), size, esc(mtime),
                '<D:collection/>' if is_dir else '', 1 if is_dir else 0,
                esc('httpd/unix-directory' if is_dir
                    else guess_content_type(name))))

    def _dav_options(self):
        self.send_response(200)
        self.send_header('DAV', '1,2')
        self.send_header('MS-Author-Via', 'DAV')
        self.send_header('Allow', 'OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, '
                                  'PROPPATCH, MKCOL, COPY, MOVE, LOCK, UNLOCK')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _dav_propfind(self):
        if not self._dav_auth():
            return
        rel = self._dav_rel()
        depth = (self.headers.get('Depth') or '1').strip().lower()
        local = self._dav_local()
        # 全盘模式下「此电脑」根是虚拟节点，没有实体路径，要单独处理
        if local._is_virtual_root(rel):
            parts = [self._dav_entry_xml(None, '', 'LanShare')]
            if depth != '0':
                for e in local.list_drives_entries():
                    # 对外用不带冒号的盘符（C、D），否则资源管理器打不开
                    child_rel = self._dav_public(e['name'])
                    try:
                        cfull = local._full(child_rel)
                    except Exception:
                        cfull = None
                    # 显示名也不能带冒号——Windows 文件名不允许 ':'，
                    # 带上会让资源管理器直接判定整份列表无效
                    label = (e['name'] or '').rstrip(':') or e['name']
                    parts.append(self._dav_entry_xml(cfull, child_rel, label))
            return self._dav_xml(
                '<D:multistatus xmlns:D="DAV:">%s</D:multistatus>'
                % ''.join(parts))
        try:
            full = self._dav_full(rel)
        except ValueError:
            # 路径本身不合法（例如全盘模式下根目录冒出的 Desktop.ini），
            # 按"不存在"回复；返回 403 会让资源管理器把整个目录判为无效
            return self._dav_status(404)
        except Exception as exc:
            return self._dav_status(403, str(exc).encode('utf-8'))
        if not os.path.exists(full):
            return self._dav_status(404)
        parts = []
        try:
            if os.path.isdir(full):
                here = self._dav_public(rel)
                # 盘符根目录的显示名用 'C' 这种无冒号形式
                base = os.path.basename(full)
                if not base and len(here) == 1 and here.isalpha():
                    base = here.upper()
                parts.append(self._dav_entry_xml(
                    full, here, base or 'LanShare'))
                if depth != '0':
                    for e in local.list_dir(rel, include_hidden=True):
                        child_rel = join_rel(rel, e['name'])
                        try:
                            cfull = local._full(child_rel)
                        except Exception:
                            continue
                        parts.append(self._dav_entry_xml(
                            cfull, self._dav_public(child_rel), e['name']))
            else:
                parts.append(self._dav_entry_xml(full, rel, os.path.basename(full)))
        except PermissionError:
            return self._dav_status(403)
        except OSError as exc:
            return self._dav_status(500, str(exc).encode('utf-8'))
        self._dav_xml('<D:multistatus xmlns:D="DAV:">%s</D:multistatus>'
                      % ''.join(parts))

    def _dav_proppatch(self):
        if not self._dav_auth():
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        rel = self._dav_rel()
        try:
            full = self._dav_full(rel)
        except Exception:
            return self._dav_status(403)
        if not os.path.exists(full):
            return self._dav_status(404)
        # 不支持改属性，但按规范要回 207 告诉客户端"这些属性没设成功"
        body = (
            '<D:multistatus xmlns:D="DAV:"><D:response><D:href>%s</D:href>'
            '<D:propstat><D:prop/><D:status>HTTP/1.1 403 Forbidden</D:status>'
            '</D:propstat></D:response></D:multistatus>'
            % xml.sax.saxutils.escape(self._dav_href(rel, os.path.isdir(full))))
        self._dav_xml(body)

    def _dav_mkcol(self):
        if not self._dav_auth():
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        rel = self._dav_rel()
        if not rel:
            return self._dav_status(405)
        try:
            self._dav_local().mkdir(self._dav_internal(rel))
        except FileExistsError:
            return self._dav_status(405)
        except Exception as exc:
            return self._dav_status(403, str(exc).encode('utf-8'))
        self._dav_status(201)

    def _dav_move(self, copy=False):
        if not self._dav_auth():
            return
        rel = self._dav_rel()
        dest = self.headers.get('Destination') or ''
        dest_path = urllib.parse.urlparse(dest).path
        if not dest_path.startswith(DAV_BASE):
            return self._dav_status(502, b'bad destination')
        dest_rel = norm_rel(urllib.parse.unquote(dest_path[len(DAV_BASE):]))
        try:
            src_full = self._dav_full(rel)
            dst_full = self._dav_full(dest_rel)
        except Exception:
            return self._dav_status(403)
        if not os.path.exists(src_full):
            return self._dav_status(404)
        overwrite = (self.headers.get('Overwrite') or 'T').upper() != 'F'
        if os.path.exists(dst_full):
            if not overwrite:
                return self._dav_status(412)
            try:
                if os.path.isdir(dst_full):
                    shutil.rmtree(dst_full)
                else:
                    os.remove(dst_full)
            except OSError as exc:
                return self._dav_status(403, str(exc).encode('utf-8'))
        existed = os.path.exists(dst_full)
        try:
            parent = os.path.dirname(dst_full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if copy:
                if os.path.isdir(src_full):
                    shutil.copytree(src_full, dst_full)
                else:
                    shutil.copy2(src_full, dst_full)
                self._dav_status(204 if existed else 201)
            else:
                shutil.move(src_full, dst_full)
                self._dav_status(201)
        except Exception as exc:
            self._dav_status(403, str(exc).encode('utf-8'))

    def _dav_get(self, head=False):
        if not self._dav_auth():
            return
        rel = self._dav_rel()
        try:
            full = self._dav_full(rel)
        except Exception:
            return self._dav_status(403)
        if not os.path.exists(full):
            return self._dav_status(404)
        if os.path.isdir(full):
            # 目录不能用 GET 取内容；客户端应先 PROPFIND，这里回个空的 200
            return self._dav_status(200)
        try:
            size = os.path.getsize(full)
            mtime = os.path.getmtime(full)
        except OSError:
            return self._dav_status(404)
        self.send_response(200)
        self.send_header('Content-Type', guess_content_type(os.path.basename(full)))
        self.send_header('Content-Length', str(size))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Last-Modified', self.date_time_string(mtime))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if head:
            return
        try:
            with open(full, 'rb') as f:
                remaining = size
                while remaining > 0:
                    data = f.read(min(CHUNK, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _dav_put(self):
        if not self._dav_auth():
            return
        rel = self._dav_rel()
        if not rel:
            return self._dav_status(405)
        try:
            full = self._dav_full(rel)
        except Exception:
            return self._dav_status(403)
        existed = os.path.exists(full)
        length = int(self.headers.get('Content-Length') or 0)
        try:
            parent = os.path.dirname(full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            remaining = length
            with open(full, 'wb') as f:
                while remaining > 0:
                    data = self.rfile.read(min(CHUNK, remaining))
                    if not data:
                        break
                    f.write(data)
                    remaining -= len(data)
        except Exception as exc:
            return self._dav_status(500, str(exc).encode('utf-8'))
        self._dav_status(204 if existed else 201)

    def _dav_delete(self):
        if not self._dav_auth():
            return
        rel = self._dav_rel()
        try:
            full = self._dav_full(rel)
        except Exception:
            return self._dav_status(403)
        if not rel or not os.path.exists(full):
            return self._dav_status(404)
        try:
            if os.path.isdir(full) and not os.path.islink(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
        except Exception as exc:
            return self._dav_status(403, str(exc).encode('utf-8'))
        self._dav_status(204)

    def _dav_lock(self):
        if not self._dav_auth():
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        rel = self._dav_rel()
        token = 'opaquelocktoken:%s' % new_id(16)
        self._dav_locks[token] = (rel, now_ts() + 1800)
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
                '<D:locktype><D:write/></D:locktype>'
                '<D:lockscope><D:exclusive/></D:lockscope>'
                '<D:depth>infinity</D:depth>'
                '<D:timeout>Second-1800</D:timeout>'
                '<D:locktoken><D:href>%s</D:href></D:locktoken>'
                '</D:activelock></D:lockdiscovery></D:prop>' % token).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/xml; charset=utf-8')
        self.send_header('Lock-Token', '<%s>' % token)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dav_unlock(self):
        if not self._dav_auth():
            return
        token = (self.headers.get('Lock-Token') or '').strip('<>')
        self._dav_locks.pop(token, None)
        self._dav_status(204)

    def _handle(self, method):
        self._set_cookie = None
        try:
            path, query = self._query()
        except Exception:
            return self._err('非法请求', 400)
        handler = self.ROUTES.get((method, path))
        if handler is None:
            if self.headers.get('Content-Length') not in (None, '0'):
                self.close_connection = True
            return self._err('接口不存在：%s %s' % (method, path), 404)
        if path not in self.PUBLIC_PATHS and not self._authed(query):
            self.close_connection = True
            return self._send_json({'ok': False, 'error': '需要群组密钥', 'auth_required': True,
                                    'name': self.state.config.name}, status=401, close=True)
        try:
            handler(self, query)
        except RemoteError as exc:
            self._err(exc.message, exc.status if 400 <= exc.status < 600 else 502)
        except ValueError as exc:
            self._err(str(exc), 400)
        except FileNotFoundError as exc:
            self._err('文件不存在：%s' % exc, 404)
        except PermissionError as exc:
            self._err('没有权限：%s' % exc, 403)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            log.error('接口异常 %s %s：%s\n%s', method, path, exc, traceback.format_exc())
            self._err('服务器内部错误：%s' % exc, 500)

    PUBLIC_PATHS = ('/', '/index.html', '/favicon.ico', '/api/status')

    # ---- 页面 ----
    def route_index(self, query):
        body = UI_HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if query.get('key'):
            self.send_header('Set-Cookie', 'ls_key=%s; Path=/; Max-Age=31536000; SameSite=Lax'
                             % urllib.parse.quote(query['key']))
        self.end_headers()
        self.wfile.write(body)

    def route_favicon(self, query):
        body = FAVICON_SVG.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'image/svg+xml')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        self.wfile.write(body)

    # ---- 信息 ----
    def route_ping(self, query):
        cfg = self.state.config
        self._ok(app=APP_NAME, version=__version__, name=cfg.name, id=self.state.self_id,
                 port=cfg.port, auth_required=bool(cfg.key), platform=sys.platform,
                 authed=self._authed(query), time=now_ts(),
                 root='此电脑' if cfg.allow_whole_disk else basename_of(cfg.root),
                 whole_disk=cfg.allow_whole_disk,
                 # 挂载磁盘时要用：对方有哪些盘可挂
                 drives=list_drives() if cfg.allow_whole_disk else [],
                 default_incoming=cfg.default_incoming_rel())

    def route_status(self, query):
        cfg = self.state.config
        self._ok(app=APP_NAME, version=__version__, name=cfg.name,
                 auth_required=bool(cfg.key), authed=self._authed(query))

    # ---- 挂载远端磁盘（WebDAV -> 网络驱动器） ----
    def route_mount(self, query):
        if self.command == 'GET':
            return self._ok(mounted=mounted_lanshare_drives(),
                            free=list_free_drives(),
                            dav_supported=(os.name == 'nt'),
                            is_admin=is_admin())
        body = self._read_json()
        dev_id = body.get('device_id')
        drive = str(body.get('drive') or '').strip().upper()
        dev = self.state.get_device(dev_id)
        if not dev:
            return self._err('设备不存在，请刷新设备列表', 404)
        # get_device 返回的是原始记录，没有 online 字段，按 last_seen 判断
        online = (now_ts() - dev.get('last_seen', 0)) < 25
        if not online:
            return self._err('这台设备当前离线', 400)
        if not drive:
            free = list_free_drives()
            if not free:
                return self._err('没有可用的盘符了，先断掉一个吧')
            drive = free[0]
        if not re.match(r'^[A-Z]:$', drive):
            return self._err('盘符不正确：%s' % drive)
        if drive[:1] in {d[:1] for d in list_drives()}:
            return self._err('盘符 %s 已经被占用了' % drive)

        host = dev.get('host')
        port = int(dev.get('port') or DEFAULT_PORT)
        scheme = 'https' if dev.get('https') else 'http'
        base = '%s://%s:%d%s' % (scheme, host, port, DAV_BASE)
        # 指定挂载哪台电脑的哪个盘。
        # 注意：不能挂「此电脑」这个虚拟根——Windows 的 WebDAV 客户端
        # 在浏览它时会报"找不到网络路径"；挂具体某个盘（/dav/C/）才正常。
        sub = norm_rel(body.get('sub') or '')
        if sub:
            pub = sub.upper()
            if len(pub) == 2 and pub[1] == ':':
                pub = pub[0]
            url = '%s/%s/' % (base, urllib.parse.quote(pub))
        else:
            url = base + '/'
        try:
            ok, msg = mount_webdav(url, drive,
                                   username=body.get('user') or 'LanShare',
                                   password=body.get('password') or self.state.config.key)
        except Exception as exc:
            return self._err('挂载失败：%s' % exc)
        if not ok:
            return self._err(msg or '挂载失败')
        self._ok(drive=drive, url=url, sub=sub, mounted=mounted_lanshare_drives(),
                 message=msg or ('已挂载到 %s' % drive))

    def route_mount_drives(self, query):
        """列出某台远端设备可供挂载的磁盘（全盘模式下就是它的各个分区）。"""
        dev = self.state.get_device(query.get('device_id'))
        if not dev:
            return self._err('设备不存在，请刷新设备列表', 404)
        if not (now_ts() - dev.get('last_seen', 0)) < 25:
            return self._err('这台设备当前离线', 400)
        drives = []
        try:
            info = self._provider(dev, 8).ping().get('info') or {}
            if info.get('whole_disk'):
                drives = list(info.get('drives') or [])
        except Exception as exc:
            return self._err('无法连接对方：%s' % exc, 502)
        self._ok(whole_disk=bool(drives), drives=drives)

    def route_unmount(self, query):
        body = self._read_json()
        drive = str(body.get('drive') or '').strip().upper()
        if not drive:
            return self._err('缺少盘符')
        ok, msg = unmount_webdav(drive)
        if not ok:
            return self._err(msg)
        self._ok(drive=drive, message=msg, mounted=mounted_lanshare_drives())

    def route_info(self, query):
        cfg = self.state.config
        try:
            du = shutil.disk_usage(list_drives()[0] + os.sep if cfg.allow_whole_disk else cfg.root)
            disk = {'total': du.total, 'used': du.used, 'free': du.free}
        except Exception:
            disk = None
        self._ok(app=APP_NAME, version=__version__, name=cfg.name, id=self.state.self_id,
                 port=cfg.port,
                 root='此电脑（全盘）' if cfg.allow_whole_disk else basename_of(cfg.root),
                 root_full=cfg.root if cfg.allow_whole_disk else None,
                 uptime=int(now_ts() - self.state.start_time), platform=sys.platform,
                 auth_required=bool(cfg.key), key=cfg.key,
                 urls=['http://%s:%d' % (ip, cfg.port) for ip in local_ips()],
                 local_ips=local_ips(), disk=disk, auto_accept=cfg.auto_accept,
                 incoming_dir=cfg.incoming_dir, https=cfg.https,
                 allow_whole_disk=cfg.allow_whole_disk, version_full=__version__,
                 discovery=cfg.discovery, config_path=cfg.path,
                 drives=list_drives())

    def route_config(self, query):
        cfg = self.state.config
        if self.command == 'GET':
            return self._ok(config=cfg.to_dict())
        body = self._read_json()
        restart_needed = False
        if 'name' in body and str(body['name']).strip():
            cfg.name = str(body['name']).strip()[:64]
        if 'auto_accept' in body:
            cfg.auto_accept = bool(body['auto_accept'])
        if 'incoming_dir' in body:
            cfg.incoming_dir = norm_rel(str(body['incoming_dir'])) or ''
        if 'allow_whole_disk' in body:
            cfg.allow_whole_disk = bool(body['allow_whole_disk'])
        if 'discovery' in body and bool(body['discovery']) != cfg.discovery:
            cfg.discovery = bool(body['discovery'])
            restart_needed = True
        if 'key' in body:
            new_key = str(body['key']).strip()
            if new_key != cfg.key:
                if new_key and len(new_key) > 128:
                    return self._err('访问密码太长了（最多 128 个字符）')
                cfg.key = new_key
        cfg.save()
        self._ok(config=cfg.to_dict(), restart_needed=restart_needed)

    # ---- 本机文件 ----
    def _local(self):
        cfg = self.state.config
        return LocalDirectory(cfg.root, whole_disk=cfg.allow_whole_disk)

    def route_list(self, query):
        rel = norm_rel(query.get('path', ''))
        hidden = query.get('hidden') in ('1', 'true', 'yes')
        entries = self._local().list_dir(rel, hidden)
        parts = rel_parts(rel)
        cfg = self.state.config
        return self._ok(path=rel, entries=entries,
                        root='此电脑' if cfg.allow_whole_disk else basename_of(cfg.root),
                        whole_disk=cfg.allow_whole_disk,
                        parents=[{'name': parts[i], 'path': '/'.join(parts[:i + 1])}
                                 for i in range(len(parts))])

    def route_stat(self, query):
        self._ok(**self._local().stat_path(query.get('path', '')))

    def route_exists(self, query):
        body = self._read_json()
        p = self._local()
        base = body.get('path', '')
        out = []
        for f in body.get('files', []):
            rel = join_rel(base, f.get('rel', ''))
            try:
                st = p.stat_path(rel)
            except Exception:
                st = {'exists': False}
            same = bool(st.get('exists')) and not st.get('is_dir') and \
                int(st.get('size') or 0) == int(f.get('size') or 0)
            out.append({'rel': f.get('rel'), 'target': rel, 'exists': bool(st.get('exists')),
                        'same': same, 'size': st.get('size', 0), 'is_dir': st.get('is_dir', False)})
        self._ok(results=out)

    def route_read_batch(self, query):
        """POST /api/fs/read_batch —— 一次请求取回多个小文件。

        拉取/下载大量零散小文件时，逐个下载会有一次往返的固定开销；
        这里把一批文件打包在一条响应里返回。

        响应： [4 字节小端 header 长度][header JSON][各文件原始字节]…
        header: {"files": [{"path":..., "ok":true, "size":...}, ...]}
        """
        import struct

        body = self._read_json()
        rels = [norm_rel(r) for r in (body.get('paths') or [])]
        if not rels:
            return self._err('缺少 paths')
        local = self._local()
        metas = []
        blobs = []
        for rel in rels:
            try:
                full = local._full(rel)
                if not os.path.isfile(full):
                    metas.append({'path': rel, 'ok': False, 'error': '文件不存在'})
                    continue
                with open(full, 'rb') as f:
                    data = f.read()
                metas.append({'path': rel, 'ok': True, 'size': len(data)})
                blobs.append(data)
            except Exception as exc:
                metas.append({'path': rel, 'ok': False, 'error': str(exc)})
        head = json.dumps({'files': metas}, ensure_ascii=False).encode('utf-8')
        out = bytearray(struct.pack('<I', len(head)))
        out += head
        for data in blobs:
            out += data
        out = bytes(out)
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(out)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def route_download(self, query):
        rel = query.get('path', '')
        full = self._local()._full(rel)
        if not os.path.isfile(full):
            return self._err('文件不存在', 404)
        size = os.path.getsize(full)
        mtime = os.path.getmtime(full)
        start, end, status = 0, max(0, size - 1), 200
        rng = self.headers.get('Range')
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)$', rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start >= size and size > 0:
                    self.send_response(416)
                    self.send_header('Content-Range', 'bytes */%d' % size)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                end = min(end, max(0, size - 1))
                status = 206
        length = max(0, end - start + 1) if size else 0
        name = os.path.basename(full)
        self.send_response(status)
        self.send_header('Content-Type', guess_content_type(name))
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Last-Modified', self.date_time_string(mtime))
        self.send_header('Content-Disposition',
                         "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name))
        self.send_header('Cache-Control', 'no-store')
        if status == 206:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.end_headers()
        try:
            with open(full, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    data = f.read(min(CHUNK, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _write_stream(self, full, offset, length, task=None, total=0):
        """把请求体按 offset 写入文件；返回写入后的文件大小。"""
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        mode = 'r+b' if (offset > 0 and os.path.exists(full)) else 'wb'
        written = 0
        with open(full, mode) as f:
            if offset:
                f.seek(offset)
            remaining = length
            t0 = now_ts()
            while remaining > 0:
                data = self.rfile.read(min(CHUNK, remaining))
                if not data:
                    break
                f.write(data)
                remaining -= len(data)
                written += len(data)
                if task is not None:
                    task['sent_bytes'] = task.get('sent_bytes', 0) + len(data)
                    task['updated'] = now_ts()
                    elapsed = now_ts() - t0
                    if elapsed >= 1.0:
                        task['speed'] = written / elapsed
                        t0 = now_ts()
                    if total:
                        task['progress'] = min(100.0, (offset + written) * 100.0 / total)
        return written

    def route_upload(self, query):
        """PUT /api/fs/upload?path=&offset=N —— 浏览器分片上传，可断点续传。"""
        rel = query.get('path', '')
        if not norm_rel(rel):
            return self._err('缺少 path')
        offset = int(query.get('offset') or 0)
        length = int(self.headers.get('Content-Length') or 0)
        full = self._local()._full(rel)
        task = self.state.get_task(query['task_id']) if query.get('task_id') else None
        total = int(self.headers.get('X-LanShare-Total') or 0)
        size = self._write_stream(full, offset, length, task=task, total=total)
        if task is not None:
            task['speed'] = 0
            if offset + size >= (total or offset + size) and total:
                task['done_files'] = task.get('done_files', 0) + 1
                task['progress'] = min(100.0, (offset + size) * 100.0 / total)
                if task['done_files'] >= (task.get('total_files') or 1):
                    task['status'] = 'done'
                    task['finished'] = now_ts()
            self.state.save_tasks()
        self._ok(written=offset + size, size=os.path.getsize(full))

    def route_write(self, query):
        """PUT /api/fs/write?path=&offset=N —— 供其它节点推送文件使用。"""
        cfg = self.state.config
        rel = norm_rel(query.get('path', ''))
        if not rel:
            return self._err('缺少 path')
        sender_id = self.headers.get('X-LanShare-From') or 'unknown'
        sender_name = urllib.parse.unquote(self.headers.get('X-LanShare-From-Name') or '')
        sender_name = sender_name or sender_id[:6]
        if not cfg.auto_accept and not self.state.find_receive_task(sender_id):
            return self._err('对方未开启“允许其它设备写入”，请在其网页的“传输”页确认后重试', 403)
        offset = int(query.get('offset') or 0)
        length = int(self.headers.get('Content-Length') or 0)
        total = int(self.headers.get('X-LanShare-Total') or 0)
        full = self._local()._full(rel)
        task = self.state.find_receive_task(sender_id)
        if task is None:
            task = self.state.new_task('receive', path='', remote_id=sender_id,
                                       remote_name=sender_name, total_bytes=total,
                                       total_files=1, direction='in')
        task['current'] = rel
        size = self._write_stream(full, offset, length, task=task, total=total)
        task['speed'] = 0
        final = os.path.getsize(full)
        if total and final >= total:
            done = set(task.get('done_list') or [])
            done.add(rel)
            task['done_list'] = sorted(done)
            task['done_files'] = len(done)
            if task['done_files'] >= (task.get('total_files') or 1):
                task['status'] = 'done'
                task['finished'] = now_ts()
                task['progress'] = 100.0
        # 每个分片都会走到这里，落盘做节流；但任务完成时必须立即落盘，
        # 否则最后一秒内的状态可能来不及写下来。
        self.state.save_tasks_soon(force=(task.get('status') == 'done'))
        self._ok(written=offset + size, size=final)

    def route_write_batch(self, query):
        """PUT /api/fs/write_batch —— 一次请求写入多个小文件。

        大量零散小文件时，逐个文件一次 HTTP 往返会让速度掉到很低；
        这里把一批文件打包成一条请求，把几百次往返压成几次。

        格式：请求体为 [4 字节小端 header 长度][header JSON][各文件原始字节]…
        header: {"files": [{"path":..., "size":..., "offset":..., "total":...}, ...]}
        """
        import struct

        cfg = self.state.config
        sender_id = self.headers.get('X-LanShare-From') or 'unknown'
        sender_name = urllib.parse.unquote(self.headers.get('X-LanShare-From-Name') or '')
        sender_name = sender_name or sender_id[:6]
        if not cfg.auto_accept and not self.state.find_receive_task(sender_id):
            return self._err('对方未开启“允许其它设备写入”，请在其网页的“传输”页确认后重试', 403)

        length = int(self.headers.get('Content-Length') or 0)
        if length <= 4:
            return self._err('请求体为空')
        raw_head = self.rfile.read(4)
        if len(raw_head) < 4:
            return self._err('请求体不完整')
        head_len = struct.unpack('<I', raw_head)[0]
        if head_len <= 0 or head_len > length - 4:
            return self._err('批量头部长度非法')
        head_raw = self._read_exact(head_len)
        try:
            head = json.loads(head_raw.decode('utf-8'))
        except Exception:
            return self._err('批量头部不是合法 JSON')
        items = head.get('files') or []
        if not items:
            return self._err('批量内容为空')

        task = self.state.find_receive_task(sender_id)
        if task is None:
            task = self.state.new_task('receive', path='', remote_id=sender_id,
                                       remote_name=sender_name,
                                       total_bytes=int(head.get('total_bytes') or 0),
                                       total_files=int(head.get('total_files') or len(items)),
                                       direction='in')

        local = self._local()
        written_total = 0
        results = []
        for it in items:
            rel = norm_rel(it.get('path', ''))
            payload_len = int(it.get('size') or 0)
            if not rel or payload_len < 0:
                results.append({'path': rel, 'ok': False, 'error': '参数非法'})
                if payload_len > 0:
                    self._read_exact(payload_len)
                continue
            data = self._read_exact(payload_len) if payload_len else b''
            try:
                full = local._full(rel)
                parent = os.path.dirname(full)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                offset = int(it.get('offset') or 0)
                mode = 'r+b' if (offset > 0 and os.path.exists(full)) else 'wb'
                with open(full, mode) as f:
                    if offset:
                        f.seek(offset)
                    f.write(data)
                written_total += len(data)
                results.append({'path': rel, 'ok': True, 'written': offset + len(data)})
                done = set(task.get('done_list') or [])
                done.add(rel)
                task['done_list'] = sorted(done)
                task['done_files'] = len(done)
                task['sent_bytes'] = task.get('sent_bytes', 0) + len(data)
                task['current'] = rel
                task['updated'] = now_ts()
            except Exception as exc:
                results.append({'path': rel, 'ok': False, 'error': str(exc)})

        if task.get('total_files') and task['done_files'] >= task['total_files']:
            task['status'] = 'done'
            task['finished'] = now_ts()
            task['progress'] = 100.0
        self.state.save_tasks_soon(force=(task.get('status') == 'done'))
        self._ok(written=written_total, results=results,
                 done_files=task.get('done_files'))

    def route_mkdir(self, query):
        body = self._read_json()
        rel = body.get('path', '')
        if not norm_rel(rel):
            return self._err('缺少目录名')
        self._local().mkdir(rel)
        self._ok(path=norm_rel(rel))

    def route_delete(self, query):
        body = self._read_json()
        rel = norm_rel(body.get('path', ''))
        if not rel:
            return self._err('不能删除根目录')
        self._local().remove(rel)
        self._ok(path=rel)

    def route_rename(self, query):
        body = self._read_json()
        if not body.get('name'):
            return self._err('缺少新名称')
        target = self._local().rename(body.get('path', ''), body['name'])
        if self.state.config.allow_whole_disk:
            rel_new = norm_rel(target)
        else:
            rel_new = norm_rel(os.path.relpath(target, self.state.config.root))
        self._ok(path=rel_new)

    # ---- 设备 ----
    def route_devices(self, query):
        if self.command == 'GET':
            return self._ok(self_id=self.state.self_id, devices=self.state.devices_list())
        body = self._read_json()
        host = (body.get('host') or '').strip()
        if not host:
            return self._err('缺少 host')
        port = body.get('port')
        if ':' in host and not port:
            host, _, p = host.partition(':')
            port = p
        port = int(port or DEFAULT_PORT)
        probe = {'id': 'probe', 'name': host, 'host': host, 'port': port,
                 'https': bool(body.get('https'))}
        try:
            info = RemoteDirectory(probe, key=body.get('key') or self.state.config.key,
                                   timeout=6).ping()
        except RemoteError as exc:
            if exc.status == 401:
                return self._err('对方需要正确的群组密钥，请填写对方的密钥', 401)
            return self._err('连接失败：%s' % exc.message, 502)
        except Exception as exc:
            return self._err('连接失败：%s' % exc, 502)
        pong = info.get('info') or {}
        dev = self.state.upsert_device({
            'id': pong.get('id'), 'name': body.get('name') or pong.get('name'),
            'port': port, 'https': probe['https'],
            'version': pong.get('version'), 'platform': pong.get('platform'),
            'label': pong.get('root') or '',
            'whole_disk': pong.get('whole_disk', False),
        }, ip=host, port=port, source='manual')
        self._ok(device=dev, rtt=info.get('rtt'))

    def route_device_ping(self, query):
        dev = self.state.get_device(query.get('id', ''))
        if not dev:
            return self._err('设备不存在，请重新发现', 404)
        try:
            info = self._provider(dev, 6).ping()
        except Exception as exc:
            return self._err('无法连接 %s:%s —— %s' % (dev.get('host'), dev.get('port'), exc), 502)
        self._ok(rtt=info.get('rtt'), info=info.get('info'))

    def _provider(self, dev, timeout=HTTP_TIMEOUT):
        return RemoteDirectory(dev, key=self.state.config.key, timeout=timeout)

    def route_remote_list(self, query):
        dev = self.state.get_device(query.get('id', ''))
        if not dev:
            return self._err('设备不存在，请刷新设备列表', 404)
        rel = norm_rel(query.get('path', ''))
        entries = self._provider(dev).list_dir(rel, query.get('hidden') in ('1', 'true'))
        parts = rel_parts(rel)
        self._ok(path=rel, entries=entries, device={'id': dev['id'], 'name': dev.get('name')},
                 parents=[{'name': parts[i], 'path': '/'.join(parts[:i + 1])}
                          for i in range(len(parts))])

    # ---- 传输：推送 ----
    def route_send(self, query):
        body = self._read_json()
        src_rel = norm_rel(body.get('path', ''))
        targets = body.get('targets') or []
        if isinstance(targets, str):
            targets = [targets]
        if not targets:
            return self._err('请至少选择一台接收设备')
        filter_spec = body.get('filter') or None
        overwrite = body.get('overwrite') or 'skip'
        dest_base = norm_rel(body.get('dest') or '')
        src_device_id = body.get('src_device') or ''
        if src_device_id and src_device_id != self.state.self_id:
            sdev = self.state.get_device(src_device_id)
            if not sdev:
                return self._err('源设备不存在', 404)
            src = self._provider(sdev, 120)
            src_label = sdev.get('name')
        else:
            src = LocalDirectory(self.state.config.root,
                                 whole_disk=self.state.config.allow_whole_disk)
            src_label = self.state.config.name

        payload = build_payload(src, src_rel, filter_spec)
        if not payload['files'] and not payload['dirs']:
            return self._err('没有匹配到任何文件', 400)
        total_bytes = sum(int(f.get('size') or 0) for f in payload['files'])
        results = []
        for target in targets:
            if target == self.state.self_id:
                results.append({'target': target, 'error': '不能发送给自己'})
                continue
            tdev = self.state.get_device(target)
            if not tdev:
                results.append({'target': target, 'error': '设备不存在或已离线'})
                continue
            task = self.state.new_task(
                'send', path=src_rel, src=src_label, remote_id=tdev.get('id'),
                remote_name=tdev.get('name'), host=tdev.get('host'), port=tdev.get('port'),
                total_bytes=total_bytes, total_files=len(payload['files']),
                dest=norm_rel(join_rel(dest_base, payload['base_name'])),
                dest_base=dest_base, src_device=src_device_id,
                overwrite=overwrite)
            threading.Thread(target=self._run_send, name='ls-send-%s' % task['id'], daemon=True,
                             args=(task, src, tdev, payload, dest_base, overwrite,
                                   self.state.self_id, self.state.config.name)).start()
            results.append({'target': tdev.get('id'), 'name': tdev.get('name'), 'task': task['id'],
                            'files': len(payload['files']), 'bytes': total_bytes})
        self._ok(tasks=results, files=len(payload['files']), bytes=total_bytes)

    def _run_send(self, task, src, tdev, payload, dest_base, overwrite, self_id, self_name):
        try:
            dst = RemoteDirectory(tdev, key=self.state.config.key, timeout=120)
            cfg = self.state.config
            sender = {'id': self_id, 'name': self_name, 'host': primary_ip(),
                      'port': cfg.port, 'https': cfg.https}
            # 通知对方（对方未开启自动接收时会挂起为待确认）
            pending = False
            try:
                manifest = list(payload['files']) + [
                    {'rel': d['rel'], 'is_dir': True, 'size': 0} for d in payload['dirs']]
                resp = dst.announce_incoming(sender, manifest, dest_base, overwrite)
                pending = resp.get('status') == 'pending'
                # 对端可能把空的接收目录落到默认位置（例如全盘模式的 C:/LanShare 接收），
                # 以对端返回的最终目录为准，否则续传检测会查错地方
                if resp.get('path'):
                    dest_base = norm_rel(resp['path'])
                    task['dest'] = norm_rel(join_rel(dest_base, payload['base_name']))
            except RemoteError as exc:
                if exc.status not in (404, 403):
                    raise
                if exc.status == 403:
                    pending = True
            if pending:
                task['status'] = 'done'
                task['finished'] = now_ts()
                task['note'] = '对方未开启自动接收，已发出接收请求，等待对方在“传输”页确认'
                return

            ok, skipped, errors, moved = run_transfer(src, dst, payload, task,
                                                      dest_base=dest_base, sender=sender,
                                                      overwrite=overwrite)
            task['skipped'] = skipped
            task['errors'] = (task.get('errors') or []) + errors
            task['status'] = 'canceled' if task.get('cancel') else ('error' if errors else 'done')
            task['progress'] = 100.0 if task['status'] == 'done' else task.get('progress', 0.0)
            task['finished'] = now_ts()
            log.info('发送任务 %s 结束：%s（成功 %d，跳过 %d，失败 %d，共 %s）',
                     task['id'], task['status'], ok, skipped, len(errors), human_size(moved))
        except Exception as exc:
            task['status'] = 'error'
            task['errors'] = (task.get('errors') or []) + [str(exc)]
            log.warning('发送任务 %s 异常：%s', task['id'], exc)
        finally:
            try:
                dst.close()
            except Exception:
                pass
            self.state.save_tasks()

    # ---- 传输：拉取 ----
    def route_pull(self, query):
        body = self._read_json()
        dev_id = body.get('device_id')
        paths = body.get('paths') or []
        if isinstance(paths, str):
            paths = [paths]
        paths = [norm_rel(p) for p in paths if p is not None]
        if not paths:
            return self._err('请选择要拉取的文件或目录')
        dev = self.state.get_device(dev_id)
        if not dev:
            return self._err('设备不存在，请刷新设备列表', 404)
        # 拉取到自己这边：空值回落到默认接收目录（全盘模式下必须带盘符）
        dest = self.state.config.dest_base(body.get('dest') or '')
        filter_spec = body.get('filter') or None
        task = self.state.new_task('pull', remote_id=dev['id'], remote_name=dev.get('name'),
                                   dest=dest, paths=paths, filter=filter_spec)
        threading.Thread(target=self._run_pull, name='ls-pull-%s' % task['id'], daemon=True,
                         args=(task, dev, paths, dest, filter_spec)).start()
        self._ok(task=task['id'], count=len(paths))

    def _run_pull(self, task, dev, paths, dest, filter_spec):
        try:
            src = self._provider(dev, 120)
            dst = LocalDirectory(self.state.config.root,
                                 whole_disk=self.state.config.allow_whole_disk)
            total = 0
            jobs = []
            for p in paths:
                pl = build_payload(src, p, filter_spec, should_stop=lambda: task.get('cancel'))
                total += sum(int(f.get('size') or 0) for f in pl['files'])
                jobs.append(pl)
            task['total_bytes'] = total
            task['total_files'] = sum(len(j['files']) for j in jobs)
            if not task['total_files']:
                task['status'] = 'error'
                task['errors'] = ['没有匹配到任何文件']
                return
            ok = skipped = 0
            errors = []
            moved = 0
            for pl in jobs:
                if task.get('cancel'):
                    break
                o, s, e, m = run_transfer(src, dst, pl, task, dest_base=dest)
                ok += o
                skipped += s
                errors += e
                moved += m
            task['errors'] = (task.get('errors') or []) + errors
            task['status'] = 'canceled' if task.get('cancel') else ('error' if errors else 'done')
            task['progress'] = 100.0 if task['status'] == 'done' else task.get('progress', 0.0)
            task['finished'] = now_ts()
            log.info('拉取任务 %s 结束：%s（成功 %d，跳过 %d，失败 %d，共 %s）',
                     task['id'], task['status'], ok, skipped, len(errors), human_size(moved))
        except Exception as exc:
            task['status'] = 'error'
            task['errors'] = (task.get('errors') or []) + [str(exc)]
            log.warning('拉取任务 %s 异常：%s', task['id'], exc)
        finally:
            try:
                src.close()
            except Exception:
                pass
            self.state.save_tasks()

    # ---- 传输：接收登记（对端推送前调用） ----
    def route_receive(self, query):
        body = self._read_json()
        sender_id = body.get('from')
        sender_name = body.get('from_name') or '对方设备'
        files = body.get('files') or []
        dest = self.state.config.dest_base(body.get('path') or '')
        overwrite = body.get('overwrite') or 'skip'
        # 记住来源设备的地址，以便用户确认后能回调拉取
        peer = {'id': sender_id, 'name': sender_name,
                'host': body.get('from_host') or self.client_address[0],
                'port': body.get('from_port') or DEFAULT_PORT,
                'https': bool(body.get('from_https'))}
        if self.state.get_device(sender_id) is None:
            self.state.upsert_device(peer, ip=peer['host'], port=peer['port'], source='incoming')
        if not self.state.config.auto_accept:
            task = self.state.new_task(
                'receive', status='pending', path=dest, remote_id=sender_id,
                remote_name=sender_name, total_files=len(files),
                total_bytes=sum(int(f.get('size') or 0) for f in files),
                direction='pending', files=files[:2000], overwrite=overwrite,
                note='对方请求发送文件，等待你确认')
            return self._ok(status='pending', task=task['id'], path=dest)
        task = self.state.find_receive_task(sender_id)
        if task is None:
            task = self.state.new_task(
                'receive', path=dest, remote_id=sender_id, remote_name=sender_name,
                total_files=len(files),
                total_bytes=sum(int(f.get('size') or 0) for f in files),
                direction='in', files=files[:2000], overwrite=overwrite)
        return self._ok(status='accepted', task=task['id'], path=dest)

    # ---- 任务 ----
    def route_tasks(self, query):
        if self.command == 'GET':
            return self._ok(tasks=self.state.tasks_list())
        body = self._read_json()
        action = body.get('action')
        task_id = body.get('id')
        if task_id == 'all':
            for t in self.state.tasks_list():
                if t['status'] in ('running', 'paused'):
                    if action == 'cancel':
                        t['cancel'] = True
                        t['status'] = 'canceled'
                    elif action == 'pause':
                        t['pause'] = True
                        t['status'] = 'paused'
                    elif action == 'resume':
                        t['pause'] = False
                        t['status'] = 'running'
            self.state.save_tasks()
            return self._ok(action=action)
        task = self.state.get_task(task_id)
        if not task:
            return self._err('任务不存在', 404)
        if action == 'cancel':
            task['cancel'] = True
            task['status'] = 'canceled'
        elif action == 'pause':
            task['pause'] = True
            task['status'] = 'paused'
        elif action == 'resume':
            task['pause'] = False
            task['status'] = 'running'
        elif action == 'delete':
            with self.state.lock:
                self.state.tasks.pop(task_id, None)
        elif action == 'accept':
            dev = self.state.get_device(task.get('remote_id'))
            if not dev:
                return self._err('来源设备已离线，无法拉取', 404)
            files = task.get('files') or []
            task['status'] = 'running'
            task['direction'] = 'in'
            task['done_list'] = []
            paths = sorted({f['rel'] for f in files})
            dest = task.get('path') or ''
            if not dest and paths:
                top = paths[0].split('/')[0]
                if all(p == top or p.startswith(top + '/') for p in paths):
                    dest = top if any(f.get('is_dir') for f in files) else ''
                    paths = paths if dest else paths
            threading.Thread(target=self._run_accept, daemon=True,
                             args=(task, dev, files, task.get('path') or '')).start()
        elif action == 'reject':
            task['status'] = 'canceled'
            task['note'] = '已拒绝接收'
        self.state.save_tasks()
        self._ok(task=task)

    def _run_accept(self, task, dev, files, dest):
        """用户确认后，从发送端拉取文件到本机。"""
        try:
            src = self._provider(dev, 120)
            dst = LocalDirectory(self.state.config.root,
                                 whole_disk=self.state.config.allow_whole_disk)
            dirs = [{'rel': f['rel']} for f in files if f.get('is_dir')]
            fl = [{'rel': f['rel'], 'size': f.get('size', 0), 'mtime': f.get('mtime', 0)}
                  for f in files if not f.get('is_dir')]
            # flat：manifest 里的 rel 已经是相对发送端源根的完整路径，直接平铺到 dest 下
            payload = {'base_rel': '', 'base_name': '', 'dirs': dirs, 'files': fl, 'flat': True}
            task['total_bytes'] = sum(int(f.get('size') or 0) for f in fl)
            task['total_files'] = len(fl)
            ok, skipped, errors, moved = run_transfer(src, dst, payload, task, dest_base=dest)
            task['errors'] = (task.get('errors') or []) + errors
            task['status'] = 'canceled' if task.get('cancel') else ('error' if errors else 'done')
            task['progress'] = 100.0 if task['status'] == 'done' else task.get('progress', 0.0)
            task['finished'] = now_ts()
        except Exception as exc:
            task['status'] = 'error'
            task['errors'] = (task.get('errors') or []) + [str(exc)]
        finally:
            try:
                src.close()
            except Exception:
                pass
            self.state.save_tasks()

    ROUTES = {
        ('GET', '/'): route_index,
        ('GET', '/index.html'): route_index,
        ('GET', '/favicon.ico'): route_favicon,
        ('GET', '/api/ping'): route_ping,
        ('GET', '/api/status'): route_status,
        ('GET', '/api/mount'): route_mount,
        ('POST', '/api/mount'): route_mount,
        ('GET', '/api/mount/drives'): route_mount_drives,
        ('POST', '/api/unmount'): route_unmount,
        ('GET', '/api/info'): route_info,
        ('GET', '/api/config'): route_config,
        ('POST', '/api/config'): route_config,
        ('GET', '/api/fs/list'): route_list,
        ('GET', '/api/fs/stat'): route_stat,
        ('POST', '/api/fs/exists'): route_exists,
        ('GET', '/api/fs/download'): route_download,
        ('POST', '/api/fs/read_batch'): route_read_batch,
        ('PUT', '/api/fs/upload'): route_upload,
        ('PUT', '/api/fs/write'): route_write,
        ('PUT', '/api/fs/write_batch'): route_write_batch,
        ('POST', '/api/fs/mkdir'): route_mkdir,
        ('POST', '/api/fs/delete'): route_delete,
        ('POST', '/api/fs/rename'): route_rename,
        ('GET', '/api/devices'): route_devices,
        ('POST', '/api/devices'): route_devices,
        ('GET', '/api/devices/ping'): route_device_ping,
        ('GET', '/api/remote/list'): route_remote_list,
        ('POST', '/api/send'): route_send,
        ('POST', '/api/pull'): route_pull,
        ('POST', '/api/receive'): route_receive,
        ('GET', '/api/tasks'): route_tasks,
        ('POST', '/api/tasks'): route_tasks,
    }


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout)):
            return
        log.debug('连接处理异常 %s：%s', client_address, exc)


# ---------------------------------------------------------------------------
# 自签名证书（可选，需 cryptography）
# ---------------------------------------------------------------------------

def ensure_cert(cert_dir):
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, 'cert.pem')
    key_path = os.path.join(cert_dir, 'key.pem')
    if os.path.exists(cert_path) and os.path.exists(key_path):
        try:
            if time.time() - os.path.getmtime(cert_path) < 300 * 86400:
                return cert_path, key_path
        except Exception:
            pass
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        log.warning('未安装 cryptography，无法生成自签名证书，将使用 HTTP')
        return None, None
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LanShare')])
    sans = [x509.DNSName(socket.gethostname())]
    for ip in local_ips() + ['127.0.0.1']:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except Exception:
            pass
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3000))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .sign(key, hashes.SHA256()))
    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    log.info('已生成自签名证书：%s', cert_path)
    return cert_path, key_path


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog='lanshare',
        description='LanShare —— 局域网多设备文件互查/互传工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例：\n'
               '  python lanshare.py --root "D:\\共享" --open\n'
               '  python lanshare.py --port 9000 --name 客厅电脑\n'
               '  python lanshare.py --no-auth\n')
    p.add_argument('root', nargs='?', default=None, help='要共享的目录（默认：当前目录）')
    p.add_argument('-r', '--root', dest='root_opt', default=None, help='要共享的目录')
    p.add_argument('-p', '--port', type=int, default=None, help='监听端口（默认 %d）' % DEFAULT_PORT)
    p.add_argument('-n', '--name', default=None, help='本机在局域网中显示的名字')
    p.add_argument('-k', '--key', default=None, help='群组密钥（默认不设，局域网内直接可访问）')
    p.add_argument('--no-auth', action='store_true', help='不设密钥（默认行为）')
    p.add_argument('--auth', action='store_true',
                   help='启用群组密钥保护（默认关闭；启用后其它设备需填同一个密钥）')
    p.add_argument('--whole-disk', action='store_true', default=None,
                   help='允许访问本机所有磁盘（默认开启）')
    p.add_argument('--shared-only', action='store_true',
                   help='只允许访问 --root 指定的目录（默认是全盘）')
    p.add_argument('--https', action='store_true', help='启用自签名 HTTPS')
    p.add_argument('--no-https', action='store_true', help='强制使用 HTTP')
    p.add_argument('--no-discovery', action='store_true', help='关闭 UDP 广播自动发现')
    p.add_argument('--peer', action='append', default=[], metavar='IP[:PORT]',
                   help='直接连接指定设备（可重复），例如 --peer 192.168.3.6。'
                        '自动发现不可用时用这个；对方端口不同可写 192.168.3.6:9000')
    p.add_argument('--confirm', '--confirm-receive', dest='confirm', action='store_true',
                   help='其它设备推送文件前需要在本机网页上手动确认')
    p.add_argument('--open', action='store_true', help='启动后自动打开浏览器')
    p.add_argument('--tray', '--silent', dest='tray', action='store_true',
                   help='静默启动：不显示窗口，只在右下角托盘运行（开机自启用这个）')
    p.add_argument('--no-gui', action='store_true',
                   help='不使用桌面界面，仅后台运行服务（调试用）')
    p.add_argument('--config', default=None, help='配置文件路径')
    p.add_argument('-v', '--verbose', action='store_true', help='输出调试日志')
    p.add_argument('--version', action='version', version='%s %s' % (APP_NAME, __version__))
    return p.parse_args(argv)


def main(argv=None):
    setup_console_encoding()
    args = parse_args(argv)

    global GUI_MODE
    # 打包成 exe 双击运行时：走桌面界面（原生窗口 + 托盘），不弹控制台
    if getattr(sys, 'frozen', False) and not args.no_gui:
        GUI_MODE = True
    if args.no_gui:
        GUI_MODE = False

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s  %(levelname)-7s %(message)s', datefmt='%H:%M:%S',
                        filename=None if not GUI_MODE else
                        os.path.join(tempfile.gettempdir(), 'lanshare.log'),
                        filemode='a')
    # 数据目录：打包后写到 %LOCALAPPDATA%\LanShare，避免污染 exe 所在目录
    if getattr(sys, 'frozen', False):
        script_dir = os.path.join(
            os.environ.get('LOCALAPPDATA') or tempfile.gettempdir(), 'LanShare')
        try:
            os.makedirs(script_dir, exist_ok=True)
        except OSError:
            script_dir = tempfile.gettempdir()
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = Config(script_dir)
    if args.config:
        cfg.path = os.path.abspath(args.config)
        cfg.load()
        cfg._migrate()

    # 模式：默认全盘可访问；--shared-only 则只用 --root 那一份
    if args.whole_disk:
        cfg.allow_whole_disk = True
    if args.shared_only:
        cfg.allow_whole_disk = False

    root = args.root_opt or args.root or cfg.root or os.getcwd()
    root = os.path.abspath(root)
    if not cfg.allow_whole_disk and not os.path.isdir(root):
        log.error('共享目录不存在：%s', root)
        return 2
    cfg.root = root
    if args.name:
        cfg.name = args.name
    if args.port:
        cfg.port = args.port
    # 密钥：默认关。只有显式给了 --key / --auth / 之前配置里存过才启用
    if args.key is not None:
        cfg.key = args.key.strip()
    if args.no_auth:
        cfg.key = ''
    if args.auth and not cfg.key:
        cfg.key = new_id(8)
        log.info('已启用群组密钥：%s', cfg.key)
    if args.no_discovery:
        cfg.discovery = False
    if args.confirm:
        cfg.auto_accept = False
    if args.https:
        cfg.https = True
    if args.no_https:
        cfg.https = False
    cfg.save()

    state = State(cfg, script_dir)
    Handler.state = state
    try:
        httpd = ThreadingHTTPServer(('0.0.0.0', cfg.port), Handler)
    except OSError as exc:
        log.error('端口 %s 无法监听：%s', cfg.port, exc)
        log.error('可换端口重试，例如：python lanshare.py --port %d', cfg.port + 1)
        return 3
    cfg.port = httpd.server_address[1]

    if cfg.https:
        cert, key = ensure_cert(os.path.join(script_dir, CONFIG_DIRNAME))
        if cert:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        else:
            cfg.https = False

    discovery = None
    if cfg.discovery:
        discovery = Discovery(state)
        discovery.start()

    peer_keeper = PeerKeeper(state, args.peer)
    peer_keeper.start()
    if peer_keeper.peers:
        # 固定对端存在时，把公共地址也打印出来，方便对方反向添加
        pass

    def janitor():
        while True:
            time.sleep(60)
            state.prune_devices()
            state.save_tasks()

    threading.Thread(target=janitor, name='ls-janitor', daemon=True).start()

    scheme = 'https' if cfg.https else 'http'
    q = ('?key=%s' % urllib.parse.quote(cfg.key)) if cfg.key else ''
    urls = ['%s://%s:%d/%s' % (scheme, ip, cfg.port, q) for ip in local_ips()]
    line = '=' * 70
    print('\n' + line)
    print('  %s %s  正在运行' % (APP_NAME, __version__))
    print('  本机名称 : %s' % cfg.name)
    if cfg.allow_whole_disk:
        print('  可访问   : 本机全部磁盘 %s' % '、'.join(list_drives()))
    else:
        print('  共享目录 : %s' % cfg.root)
    print('  访问密码 : %s' % (cfg.key if cfg.key else '无（局域网内直接访问）'))
    if os.name == 'nt':
        print('  WebDAV   : %s   （配置里的路径，挂载磁盘用）'
              % ('%s://%s:%d%s/' % (scheme, local_ips()[0], cfg.port, DAV_BASE)))
        print('              挂载命令：net use Z: "%s://%s:%d%s/" /persistent:no'
              % (scheme, local_ips()[0], cfg.port, DAV_BASE))
    print('  访问地址 :')
    for u in urls:
        print('      %s' % u)
    print('  ── 手机/其它电脑连同一个 WiFi/局域网，浏览器打开上面任一地址即可')
    if cfg.key:
        print('  ── 其它设备需使用相同的群组密钥：--key %s' % cfg.key)
    if peer_keeper.peers:
        print('  固定对端 : %s' % '、'.join('%s:%s' % p for p in peer_keeper.peers))
    print('  按 Ctrl+C 停止\n' + line + '\n')

    if args.open and urls:
        try:
            webbrowser.open(urls[0])
        except Exception:
            pass

    # 桌面界面（原生窗口 + 右下角托盘）。没有它就在后台跑服务。
    gui = None
    if GUI_MODE:
        try:
            from lanshare_gui import MainWindow, Tray
            holder = {}

            def _on_quit():
                holder.get('quit', lambda: None)()

            gui = MainWindow(state, cfg, {'httpd': httpd, 'discovery': discovery,
                                          'peer_keeper': peer_keeper}, _on_quit)
            tray = Tray(gui, state, cfg)
            if not tray.start():
                # 没有托盘就退化成「关闭窗口即退出」，避免用户关不掉程序
                gui.root.protocol('WM_DELETE_WINDOW', gui._really_quit)
            gui._tray_notify = tray.notify

            def _quit_all():
                try:
                    tray.stop()
                except Exception:
                    pass
                if discovery:
                    discovery.stop()
                peer_keeper.stop()
                try:
                    httpd.shutdown()
                except Exception:
                    pass
                try:
                    httpd.server_close()
                except Exception:
                    pass
                state.save_tasks()
                os._exit(0)

            holder['quit'] = _quit_all
            # 托盘菜单的消息（open / open_browser / copy_url / quit）由
            # MainWindow._pump 在主线程里统一处理，这里不需要再包一层。
            if args.tray:
                gui.root.withdraw()          # 静默启动：只在托盘
                gui.visible = False
            else:
                gui.visible = True
            log.info('桌面界面已启动%s', '（静默托盘模式）' if args.tray else '')
        except Exception as exc:
            log.warning('桌面界面启动失败，改为后台运行：%s', exc)
            log.debug('%s', traceback.format_exc())
            gui = None

    if gui is not None:
        # 界面在主线程跑；HTTP 服务已经在线程里了
        threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.5),
                         name='ls-http', daemon=True).start()
        try:
            gui.run()
        except KeyboardInterrupt:
            pass
        finally:
            try:
                if discovery:
                    discovery.stop()
                peer_keeper.stop()
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
            state.save_tasks()
        return 0

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print('\n正在停止…')
    finally:
        if discovery:
            discovery.stop()
        peer_keeper.stop()
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        state.save_tasks()
        print('已停止。')
    return 0


# 内嵌前端（同目录 lanshare_ui.py；打包单文件版会把二者合并）
from lanshare_ui import UI_HTML, FAVICON_SVG  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
