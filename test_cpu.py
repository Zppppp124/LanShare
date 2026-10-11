# -*- coding: utf-8 -*-
"""
CPU 占用回归测试。

背景（真实故障）：托盘消息转发写成
    while True:
        m = queue.get_nowait()
        ...
        queue.put(m)      # 把消息塞回队列 -> 下一轮立刻又拿到 -> 死循环
结果一启动就把一个 CPU 核心跑满、界面卡死。

本测试在「空闲」状态下分别测量三种模式的 CPU 占用，任何一项超过阈值即失败。
用法：python test_cpu.py [EXE路径]
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'dist', 'LanShare.exe')
# 空闲时正常应该在 1% 以下；留足余量，超过 8% 就判定异常
THRESHOLD = 8.0

k32 = ctypes.windll.kernel32
PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('  [OK]   ' if cond else '  [FAIL] ') + name
          + (('  -> ' + str(extra)) if (extra and not cond) else ''))


def cpu_seconds(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        c, e, k, u = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                   ctypes.byref(k), ctypes.byref(u)):
            return None
        ft = lambda v: (v.dwHighDateTime << 32) | v.dwLowDateTime
        return (ft(k) + ft(u)) / 1e7
    finally:
        k32.CloseHandle(h)


def pids_named(name):
    out = []
    try:
        raw = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq %s' % name,
                              '/FO', 'CSV', '/NH'],
                             capture_output=True, timeout=20).stdout
        for line in raw.decode('utf-8', 'replace').splitlines():
            line = line.strip()
            if line.startswith('"'):
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2:
                    try:
                        out.append(int(parts[1]))
                    except ValueError:
                        pass
    except Exception:
        pass
    return out


def tree_pids(root_pid, names=('LanShare.exe',)):
    """PyInstaller 单文件会派生子进程，父子一起测。"""
    pids = {root_pid}
    for n in names:
        pids |= set(pids_named(n))
    return pids


def sample_total(pids, seconds=6):
    """返回所有进程 CPU 占用之和（相对单核百分比）。"""
    base = {}
    for p in pids:
        v = cpu_seconds(p)
        if v is not None:
            base[p] = v
    if not base:
        return None
    time.sleep(seconds)
    total = 0.0
    for p, b in base.items():
        now = cpu_seconds(p)
        if now is None:
            continue
        total += max(0.0, (now - b)) / seconds * 100.0
    return total


def wait_port(port, timeout=40):
    import json
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/api/status' % port,
                                        timeout=2) as r:
                json.loads(r.read().decode('utf-8'))
                return True
        except Exception:
            time.sleep(0.4)
    return False


def case(title, args, port, wait=14):
    print('\n--- %s ---' % title)
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'ls-cpu-%d' % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    log = open(os.path.join(tmp, 'out.txt'), 'w', encoding='utf-8')
    proc = subprocess.Popen(args + ['--config', os.path.join(tmp, 'c.json'),
                                    '--port', str(port)],
                            stdout=log, stderr=subprocess.STDOUT, cwd=HERE)
    try:
        up = wait_port(port)
        check('%s：服务已就绪' % title, up)
        time.sleep(wait)
        pids = tree_pids(proc.pid)
        pct = sample_total(pids, seconds=6)
        if pct is None:
            check('%s：能读到 CPU 数据' % title, False, '进程可能已退出')
            return None
        ok = pct <= THRESHOLD
        check('%s：空闲 CPU 占用正常（%.1f%%，阈值 %.0f%%）' % (title, pct, THRESHOLD),
              ok, '占用过高，疑似死循环')
        return pct
    finally:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True, timeout=20)
        time.sleep(1.5)
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if not os.path.exists(EXE):
        print('找不到 %s' % EXE)
        return 1

    def free_port():
        import socket
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        p = s.getsockname()[1]
        s.close()
        return p

    print('CPU 占用回归测试（阈值 %.0f%%，空闲状态）' % THRESHOLD)
    results = {}
    results['纯后端'] = case('纯后端 --no-gui', [EXE, '--no-gui', '--no-discovery'], free_port())
    results['静默托盘'] = case('静默托盘 --tray', [EXE, '--tray', '--no-discovery'], free_port())
    results['桌面界面'] = case('桌面界面（默认）', [EXE, '--no-discovery'], free_port())

    print('\n' + '=' * 60)
    for k, v in results.items():
        print('  %-10s %s' % (k, ('%.1f%%' % v) if v is not None else 'N/A'))
    print('CPU 测试：通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  - ' + f)
    print('=' * 60)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
