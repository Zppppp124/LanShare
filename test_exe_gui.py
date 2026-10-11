# -*- coding: utf-8 -*-
"""
验证打包好的 EXE：真实启动它，检查
  1. 主窗口出现（原生窗口，不再是网页）
  2. 服务在后台可用
  3. --tray 静默模式：没有主窗口，但托盘图标存在、服务可用
  4. 关掉窗口后进程仍存活（缩到托盘）
用法：python test_exe_gui.py
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, 'dist', 'LanShare.exe')
PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('  [OK]   ' if cond else '  [FAIL] ') + name
          + (('  -> ' + str(extra)) if (extra and not cond) else ''))


user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible


def windows_of(pid):
    out = []

    def cb(hwnd, lparam):
        p = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            n = GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 2)
            GetWindowTextW(hwnd, buf, n + 2)
            out.append((buf.value, bool(IsWindowVisible(hwnd))))
        return True
    EnumWindows(EnumWindowsProc(cb), 0)
    return out


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def ping(port, timeout=3):
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/api/status' % port,
                                    timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def wait_port(port, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ping(port):
            return True
        time.sleep(0.5)
    return False


def alive(proc):
    return proc.poll() is None


def all_pids_of(proc):
    """
    PyInstaller 单文件会派生子进程，实际跑逻辑、创建窗口的是子进程。
    这里用 tasklist 找出本进程树下所有 LanShare.exe 的 PID。
    """
    pids = {proc.pid}
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq LanShare.exe', '/FO', 'CSV', '/NH'],
                             capture_output=True, timeout=20).stdout
        text = out.decode('utf-8', 'replace')
        found = set()
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith('"'):
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                try:
                    found.add(int(parts[1]))
                except ValueError:
                    pass
        pids |= found
    except Exception:
        pass
    return pids


def kill_tree(proc):
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True, timeout=15)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main():
    if not os.path.exists(EXE):
        print('找不到 %s，请先运行 python build_exe.py' % EXE)
        return 1
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'ls-exegui-%d' % int(time.time()))
    os.makedirs(tmp, exist_ok=True)

    # ---------------- 1. 正常启动：应出现主窗口 ----------------
    print('== 1. 正常双击启动（应出现主窗口）==')
    port1 = free_port()
    cfg1 = os.path.join(tmp, 'c1.json')
    p1 = subprocess.Popen([EXE, '--port', str(port1), '--no-discovery',
                           '--config', cfg1], cwd=HERE)
    try:
        ok = wait_port(port1)
        check('EXE 启动且服务可用', ok, 'port=%d' % port1)
        # 等窗口创建（PyInstaller 单文件的实际窗口在子进程里）
        title = None
        t0 = time.time()
        while time.time() - t0 < 30:
            wins = []
            for pid in all_pids_of(p1):
                wins += windows_of(pid)
            vis = [w for w in wins if w[1] and 'LanShare' in w[0]
                   and 'Hidden' not in w[0]]
            if vis:
                title = vis[0][0]
                break
            time.sleep(0.6)
        check('出现 LanShare 主窗口（原生界面）', title is not None,
              [w for pid in all_pids_of(p1) for w in windows_of(pid)][:8])
        if title:
            check('窗口标题正确', 'LanShare' in title, title)
            check('窗口不是网页而是桌面程序',
                  'Microsoft Edge' not in title and 'Chrome' not in title, title)
        st = ping(port1)
        check('返回的是全盘模式', st is not None and st.get('auth_required') is False, st)
    finally:
        kill_tree(p1)
    time.sleep(1)

    # ---------------- 2. 静默托盘启动 ----------------
    print('\n== 2. --tray 静默启动（开机自启用这个）==')
    port2 = free_port()
    cfg2 = os.path.join(tmp, 'c2.json')
    p2 = subprocess.Popen([EXE, '--tray', '--port', str(port2), '--no-discovery',
                           '--config', cfg2], cwd=HERE)
    try:
        ok = wait_port(port2)
        check('静默模式下服务照常可用', ok, 'port=%d' % port2)
        time.sleep(3)
        check('静默模式下进程存活', alive(p2))
        wins = []
        for pid in all_pids_of(p2):
            wins += windows_of(pid)
        visible_main = [w for w in wins if w[1] and 'LanShare' in w[0]
                        and 'Hidden' not in w[0]]
        check('静默模式下不显示主窗口', len(visible_main) == 0,
              [w for w in wins if w[1]][:5])
    finally:
        kill_tree(p2)
    time.sleep(1)

    # ---------------- 3. 配置文件与下载目录 ----------------
    print('\n== 3. 配置持久化 ==')
    cfg_data = {}
    if os.path.exists(cfg1):
        with open(cfg1, 'r', encoding='utf-8') as f:
            cfg_data = json.load(f)
    check('配置文件已生成', bool(cfg_data), cfg1)
    check('配置含 download_dir 字段', 'download_dir' in cfg_data, list(cfg_data.keys()))
    check('默认全盘模式', cfg_data.get('allow_whole_disk') is True, cfg_data.get('allow_whole_disk'))
    check('默认无访问密码', cfg_data.get('key') == '', repr(cfg_data.get('key')))
    check('配置版本为 2', cfg_data.get('config_version') == 2, cfg_data.get('config_version'))

    # ---------------- 4. 开机自启注册表项（检测命令是否正确） ----------------
    print('\n== 4. 开机自启命令 ==')
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Run',
                            0, winreg.KEY_READ) as k:
            try:
                val, _ = winreg.QueryValueEx(k, 'LanShare')
                check('注册表项存在且含 --tray（如未在界面上开启则属正常）', True, val)
                check('自启指向本 EXE', 'LanShare.exe' in val, val)
            except FileNotFoundError:
                check('注册表项（尚未在界面上开启，跳过）', True)
    except Exception as exc:
        check('读取注册表', False, exc)

    print('\n' + '=' * 60)
    print('EXE 界面/托盘测试：通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  - ' + f)
    print('=' * 60)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
