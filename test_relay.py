# -*- coding: utf-8 -*-
"""
「一台电脑集中管理多台」场景测试。

模拟用户的实际部署：几台电脑都静默跑 LanShare，坐在 A 前面就能：
  1. 看到 B、C 两台设备
  2. 浏览 B 上的文件
  3. 把 B 上的文件直接发送到 C（A 只做操作台，数据 B→C 直达）
  4. 把 B 上的文件下载到 A 的指定文件夹
  5. 把 A 的文件上传/发送到 B

用法：python test_relay.py
"""
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('  [OK]   ' if cond else '  [FAIL] ') + name
          + (('  -> ' + str(extra)) if (extra and not cond) else ''))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Node:
    """一个后台服务节点（用源码跑，方便测试）。"""

    def __init__(self, name, root, port, cfgdir):
        self.name, self.root, self.port = name, root, port
        self.cfg = os.path.join(cfgdir, 'cfg-%s.json' % name)
        self.log = open(os.path.join(cfgdir, 'log-%s.txt' % name), 'w', encoding='utf-8')
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, 'lanshare.py'),
             '--root', root, '--shared-only', '--port', str(port),
             '--name', name, '--no-discovery', '--config', self.cfg],
            stdout=self.log, stderr=subprocess.STDOUT, cwd=HERE)

    def api(self, method, path, body=None, timeout=60):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        payload = None
        h = {}
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
            h['Content-Type'] = 'application/json; charset=utf-8'
            h['Content-Length'] = str(len(payload))
        c.request(method, path, body=payload, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            return r.status, json.loads(data.decode('utf-8'))
        except Exception:
            return r.status, data

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=8)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.log.close()
        except Exception:
            pass


def wait_up(port, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            c.request('GET', '/api/status')
            if c.getresponse().status == 200:
                c.close()
                return True
            c.close()
        except Exception:
            time.sleep(0.3)
    return False


def sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    tmp = tempfile.mkdtemp(prefix='lanshare-relay-')
    print('测试目录：%s\n' % tmp)
    A_dir = os.path.join(tmp, 'A'); B_dir = os.path.join(tmp, 'B')
    C_dir = os.path.join(tmp, 'C'); dl_dir = os.path.join(tmp, 'A下载')
    for d in (A_dir, B_dir, C_dir, dl_dir):
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(B_dir, 'B的资料'), exist_ok=True)
    with open(os.path.join(B_dir, 'B的资料', '报告.txt'), 'w', encoding='utf-8') as f:
        f.write('这是 B 电脑上的报告\n' * 30)
    with open(os.path.join(B_dir, 'B的资料', '数据.bin'), 'wb') as f:
        f.write(os.urandom(2 * 1024 * 1024))
    with open(os.path.join(A_dir, 'A的文件.txt'), 'w', encoding='utf-8') as f:
        f.write('这是 A 电脑上的文件\n')

    cfgdir = os.path.join(tmp, 'cfg'); os.makedirs(cfgdir, exist_ok=True)
    pa, pb, pc = free_port(), free_port(), free_port()
    A = Node('电脑A', A_dir, pa, cfgdir)
    B = Node('电脑B', B_dir, pb, cfgdir)
    C = Node('电脑C', C_dir, pc, cfgdir)
    gui = None
    httpd = None
    try:
        check('A/B/C 三台都启动', wait_up(pa) and wait_up(pb) and wait_up(pc))
        if FAIL:
            return report(tmp)

        # A 添加 B、C 两台设备
        st, r = A.api('POST', '/api/devices', {'host': '127.0.0.1', 'port': pb})
        check('A 添加 B', st == 200 and r['device']['name'] == '电脑B', (st, r))
        id_b = r['device']['id']
        st, r = A.api('POST', '/api/devices', {'host': '127.0.0.1', 'port': pc})
        check('A 添加 C', st == 200 and r['device']['name'] == '电脑C', (st, r))
        id_c = r['device']['id']

        # ---------------- 用 GUI 的真实代码路径来操作 ----------------
        import lanshare
        from lanshare import Config, State, Handler, ThreadingHTTPServer
        import lanshare_gui

        cfg = Config(cfgdir)
        cfg.path = os.path.join(cfgdir, 'gui.json')
        cfg.name = '总控机A'
        cfg.port = free_port()
        cfg.allow_whole_disk = True
        cfg.key = ''
        cfg.discovery = False
        cfg.save()
        state = State(cfg, cfgdir)
        Handler.state = state
        httpd = ThreadingHTTPServer(('127.0.0.1', cfg.port), Handler)
        threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.3),
                         daemon=True).start()

        # 把 A 记录里已知的 B、C 灌进 GUI 的状态
        for dev in (A.api('GET', '/api/devices')[1]['devices'] or []):
            state.upsert_device(dev, ip=dev.get('host'), port=dev.get('port'), source='manual')

        gui = lanshare_gui.MainWindow(state, cfg,
                                      {'httpd': httpd, 'discovery': None, 'peer_keeper': None},
                                      lambda: None)

        def pump(cond, timeout=40):
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    gui.root.update()
                except Exception:
                    pass
                try:
                    while True:
                        m = gui.queue.get_nowait()
                        k = m[0]
                        if k == 'devices':
                            gui._render_devices(m[1])
                        elif k == 'entries':
                            gui._render_entries(m[1], m[2], m[3], m[4])
                        elif k == 'task':
                            gui._update_task_ui(m[1])
                except Exception:
                    pass
                if cond():
                    return True
                time.sleep(0.15)
            return False

        check('GUI 启动', gui is not None)
        check('等待设备列表刷新', pump(lambda: len(gui.devices) >= 2, timeout=20),
              [d.get('name') for d in gui.devices])
        names = [d.get('name') for d in gui.devices]
        check('GUI 能看到 B 和 C', '电脑B' in names and '电脑C' in names, names)

        # ---- 浏览 B 上的文件 ----
        gui.cur = {'dev': id_b, 'path': ''}
        gui.navigate('')
        check('能浏览 B 的共享目录',
              pump(lambda: any(e['name'] == 'B的资料' for e in gui.entries), timeout=25),
              [e['name'] for e in gui.entries])
        gui.navigate('B的资料')
        check('能进入 B 的子目录',
              pump(lambda: any(e['name'] == '报告.txt' for e in gui.entries), timeout=25),
              [e['name'] for e in gui.entries])

        # ---- 从 B 下载到 A 的指定目录 ----
        entry = [e for e in gui.entries if e['name'] == '报告.txt'][0]
        gui.remember_download_dir(dl_dir)
        t1 = lanshare_gui.GuiTask('download', 'B→A 下载', total_files=1)
        gui.tasks.append(t1)
        threading.Thread(target=gui._run_download, daemon=True,
                         args=(t1, id_b, '电脑B', [(gui._rel_of(entry), entry)], dl_dir)).start()
        check('从 B 下载到 A 完成',
              pump(lambda: t1.status in ('done', 'error'), timeout=60) and t1.status == 'done',
              '%s %s' % (t1.status, t1.errors))
        check('文件落在 A 的指定目录',
              os.path.isfile(os.path.join(dl_dir, '报告.txt')), os.listdir(dl_dir))
        if os.path.isfile(os.path.join(dl_dir, '报告.txt')):
            check('下载内容一致',
                  sha(os.path.join(dl_dir, '报告.txt')) ==
                  sha(os.path.join(B_dir, 'B的资料', '报告.txt')))

        # ---- B → C 直传（A 只做操作台，这是「一台电脑管多台」的核心）----
        target_c = state.get_device(id_c)
        gui.cur = {'dev': id_b, 'path': 'B的资料'}
        entry_bin = [e for e in gui.entries if e['name'] == '数据.bin'][0]
        t2 = lanshare_gui.GuiTask('send', 'B→C 直传', total_files=1)
        gui.tasks.append(t2)
        threading.Thread(target=gui._run_send, daemon=True,
                         args=(t2, target_c, [(gui._rel_of(entry_bin), entry_bin)], '来自B')).start()
        check('B→C 直传完成',
              pump(lambda: t2.status in ('done', 'error'), timeout=90) and t2.status == 'done',
              '%s %s' % (t2.status, t2.errors))
        out_c = os.path.join(C_dir, '来自B', 'B的资料', '数据.bin')
        alt_c = os.path.join(C_dir, '来自B', '数据.bin')
        landed = out_c if os.path.isfile(out_c) else (alt_c if os.path.isfile(alt_c) else None)
        check('文件真的到了 C 电脑', landed is not None,
              os.listdir(C_dir) if os.path.isdir(C_dir) else '无')
        if landed:
            check('B→C 传输内容一致',
                  sha(landed) == sha(os.path.join(B_dir, 'B的资料', '数据.bin')))

        # ---- A 自己的文件发给 B ----
        gui.cur = {'dev': '', 'path': lanshare_gui.abs_to_rel(A_dir)}
        gui.navigate(gui.cur['path'])
        pump(lambda: any(e['name'] == 'A的文件.txt' for e in gui.entries), timeout=25)
        entry_a = [e for e in gui.entries if e['name'] == 'A的文件.txt'][0]
        target_b = state.get_device(id_b)
        t3 = lanshare_gui.GuiTask('send', 'A→B 发送', total_files=1)
        gui.tasks.append(t3)
        threading.Thread(target=gui._run_send, daemon=True,
                         args=(t3, target_b, [(gui._rel_of(entry_a), entry_a)], '从A来')).start()
        check('A→B 发送完成',
              pump(lambda: t3.status in ('done', 'error'), timeout=60) and t3.status == 'done',
              '%s %s' % (t3.status, t3.errors))
        found_b = None
        for root_, _dirs, files_ in os.walk(B_dir):
            if 'A的文件.txt' in files_:
                found_b = os.path.join(root_, 'A的文件.txt')
                break
        check('文件到了 B 电脑', found_b is not None, os.listdir(B_dir))
    finally:
        try:
            if gui:
                gui.root.destroy()
        except Exception:
            pass
        try:
            if httpd:
                httpd.shutdown(); httpd.server_close()
        except Exception:
            pass
        for n in (A, B, C):
            n.stop()

    return report(tmp)


def report(tmp):
    print('\n' + '=' * 60)
    print('集中管理测试：通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  - ' + f)
    print('=' * 60)
    print('测试临时目录：%s' % tmp)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
