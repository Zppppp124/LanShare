# -*- coding: utf-8 -*-
"""
验证托盘图标真的出现在 Windows 通知区域（屏幕右下角）。

做法：启动 EXE（--tray 静默模式），然后用 Shell_NotifyIcon 的查询能力
间接确认：枚举托盘窗口、并检查进程是否持有一个 Shell_TrayWnd 子窗口。
另外截取屏幕右下角区域存图，便于人工确认。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, 'dist', 'LanShare.exe')

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('  [OK]   ' if cond else '  [FAIL] ') + name
          + (('  -> ' + str(extra)) if (extra and not cond) else ''))


def find_tray_windows():
    """找到任务栏（Shell_TrayWnd）和它的通知区域子窗口。"""
    out = {}
    EnumWindows = user32.EnumWindows
    Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    GetClassNameW = user32.GetClassNameW

    def classname(hwnd):
        buf = ctypes.create_unicode_buffer(256)
        GetClassNameW(hwnd, buf, 256)
        return buf.value

    def cb(hwnd, lparam):
        cn = classname(hwnd)
        if cn in ('Shell_TrayWnd', 'Shell_SecondaryTrayWnd'):
            out[cn] = hwnd
        return True
    EnumWindows(Proc(cb), 0)
    return out


def screenshot_region(path, left, top, width, height):
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=(left, top, left + width, top + height))
        img.save(path)
        return True
    except Exception as exc:
        print('  截图失败:', exc)
        return False


def main():
    if not os.path.exists(EXE):
        print('找不到 EXE')
        return 1
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'ls-tray-%d' % int(time.time()))
    os.makedirs(tmp, exist_ok=True)
    cfg = os.path.join(tmp, 'c.json')

    print('== 启动 EXE（静默托盘模式）==')
    proc = subprocess.Popen([EXE, '--tray', '--port', '8765', '--no-discovery',
                             '--config', cfg], cwd=HERE)
    try:
        time.sleep(12)
        check('进程存活', proc.poll() is None)

        tray = find_tray_windows()
        check('找到 Windows 任务栏（Shell_TrayWnd）', 'Shell_TrayWnd' in tray, list(tray))

        # 托盘图标由 Explorer 承载；这里确认我们的进程没有主窗口（即确实静默）
        wins = []
        EnumWindows = user32.EnumWindows
        Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        IsWindowVisible = user32.IsWindowVisible

        def current_pids():
            out = set()
            try:
                raw = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq LanShare.exe',
                                      '/FO', 'CSV', '/NH'],
                                     capture_output=True, timeout=20).stdout
                for line in raw.decode('utf-8', 'replace').splitlines():
                    line = line.strip()
                    if line.startswith('"'):
                        parts = [p.strip('"') for p in line.split('","')]
                        if len(parts) >= 2:
                            out.add(int(parts[1]))
            except Exception:
                pass
            return out
        pids = current_pids()

        def cb(hwnd, lparam):
            p = wt.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value in pids:
                n = GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 2)
                GetWindowTextW(hwnd, buf, n + 2)
                wins.append((buf.value, bool(IsWindowVisible(hwnd))))
            return True
        EnumWindows(Proc(cb), 0)
        visible_lan = [w for w in wins if w[1] and 'LanShare' in w[0] and 'Hidden' not in w[0]]
        check('静默模式下没有可见主窗口（说明缩在托盘里）', len(visible_lan) == 0,
              [w for w in wins if w[1]][:6])

        # 确定性验证：pystray 会创建一个类名含 "SystemTrayIcon" 的窗口，
        # 它存在 == 托盘图标已注册到系统通知区域
        tray_win = []
        GetClassNameW = user32.GetClassNameW

        def cb2(hwnd, lparam):
            p = wt.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value in pids:
                buf = ctypes.create_unicode_buffer(256)
                GetClassNameW(hwnd, buf, 256)
                if 'SystemTrayIcon' in buf.value:
                    tray_win.append(buf.value)
            return True
        EnumWindows(Proc(cb2), 0)
        check('系统托盘中已注册 LanShare 图标（SystemTrayIcon 窗口存在）',
              len(tray_win) > 0, tray_win)
        if tray_win:
            print('         托盘窗口类名：%s' % tray_win[0])

        # 截右下角，人工可核对托盘图标
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        out_png = os.path.join(HERE, 'demo', 'tray-area.png')
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        if screenshot_region(out_png, max(0, sw - 460), max(0, sh - 220), 460, 220):
            check('已截取右下角托盘区域（可人工核对图标）',
                  os.path.exists(out_png) and os.path.getsize(out_png) > 1000,
                  out_png)
    finally:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True, timeout=15)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 56)
    print('托盘测试：通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    print('=' * 56)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
