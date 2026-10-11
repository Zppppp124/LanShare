# -*- coding: utf-8 -*-
"""
桌面界面自测（不需要人盯着看）。

做法：启动真实的 GUI（Tk 窗口 + 托盘），但通过直接调用界面对象的方法来模拟点击，
      验证「浏览、下载到指定目录、上传、发送、设置、托盘隐藏/恢复」这些路径。
      窗口会短暂真实显示（在 Windows 上 Tk 需要真实窗口才能工作）。

用法：python test_gui.py
"""
import os
import shutil
import socket
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


def pump_until(gui, cond, timeout=20):
    """反复抽干 GUI 消息队列并刷新界面，直到条件成立。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            gui.root.update()
        except Exception:
            pass
        try:
            gui._pump_manual()
        except Exception:
            pass
        if cond():
            return True
        time.sleep(0.15)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix='lanshare-gui-')
    src_dir = os.path.join(tmp, '源目录')
    dl_dir = os.path.join(tmp, '下载目标')
    os.makedirs(src_dir)
    os.makedirs(dl_dir)
    # 造测试数据
    with open(os.path.join(src_dir, '文档.txt'), 'w', encoding='utf-8') as f:
        f.write('GUI 测试内容\n' * 50)
    os.makedirs(os.path.join(src_dir, '子目录'))
    with open(os.path.join(src_dir, '子目录', '深层.bin'), 'wb') as f:
        f.write(os.urandom(512 * 1024))

    # 用独立配置目录，避免影响用户真实配置
    cfg_dir = os.path.join(tmp, 'cfg')
    os.makedirs(cfg_dir)
    cfg_path = os.path.join(cfg_dir, 'lanshare.config.json')

    import lanshare
    from lanshare import Config, State, ThreadingHTTPServer, Handler
    import lanshare_gui

    port = free_port()
    cfg = Config(cfg_dir)
    cfg.path = cfg_path
    cfg.name = 'GUI测试机'
    cfg.port = port
    cfg.allow_whole_disk = True
    cfg.key = ''
    cfg.discovery = False
    cfg.download_dir = ''
    cfg.root = src_dir
    cfg.save()

    state = State(cfg, cfg_dir)
    Handler.state = state
    httpd = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    cfg.port = httpd.server_address[1]
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.3), daemon=True).start()

    gui = None
    tray = None
    try:
        print('测试目录：%s\n' % tmp)

        # ---- 1. 界面能创建 ----
        quit_called = {'v': False}
        gui = lanshare_gui.MainWindow(state, cfg,
                                      {'httpd': httpd, 'discovery': None, 'peer_keeper': None},
                                      lambda: quit_called.update(v=True))
        # 给测试用的手动 pump（走和真实一样的处理逻辑）
        def manual_pump():
            try:
                while True:
                    msg = gui.queue.get_nowait()
                    kind = msg[0]
                    if kind == 'devices':
                        gui._render_devices(msg[1])
                    elif kind == 'entries':
                        gui._render_entries(msg[1], msg[2], msg[3], msg[4])
                    elif kind == 'task':
                        gui._update_task_ui(msg[1])
                    elif kind == 'added':
                        gui.refresh_devices()
                    elif kind == 'close_dialog':
                        try:
                            msg[1].destroy()
                        except Exception:
                            pass
                    elif kind == 'dialog_error':
                        try:
                            msg[1].config(text=msg[2])
                        except Exception:
                            pass
                    elif kind == 'enable_btn':
                        try:
                            msg[1].config(state='normal')
                        except Exception:
                            pass
            except Exception:
                pass
        gui._pump_manual = manual_pump
        check('主窗口创建成功', gui.root is not None)
        check('窗口标题正确', 'LanShare' in gui.root.title(), gui.root.title())

        # ---- 2. 设备列表里至少有「本机」 ----
        pump_until(gui, lambda: gui.dev_list.size() >= 1, timeout=10)
        items = list(gui.dev_list.get(0, 'end'))
        check('设备列表含本机', any('本机' in s for s in items), items)

        # ---- 3. 浏览本机（全盘模式：应列出磁盘）----
        pump_until(gui, lambda: len(gui.entries) > 0, timeout=15)
        names = [e['name'] for e in gui.entries]
        drives = [e for e in gui.entries if e.get('drive')]
        check('根位置列出磁盘', len(drives) >= 1, names)
        check('文件树有内容', len(gui.tree.get_children()) >= 1,
              gui.tree.get_children())

        # ---- 4. 进入测试目录并浏览 ----
        gui.cur = {'dev': '', 'path': lanshare_gui.abs_to_rel(src_dir)}
        gui.navigate(gui.cur['path'])
        pump_until(gui, lambda: any(e['name'] == '文档.txt' for e in gui.entries), timeout=15)
        got = [e['name'] for e in gui.entries]
        check('能浏览指定目录', '文档.txt' in got and '子目录' in got, got)

        # ---- 5. 下载到指定目录（这是用户新提的需求）----
        # 走真实的「记住下载目录」路径（download_selected 里就是调它）
        gui.remember_download_dir(dl_dir)
        sel = [e for e in gui.entries if e['name'] == '文档.txt']
        gui.tree.selection_set([iid for iid, e in
                                zip(gui.tree.get_children(), gui.entries)
                                if e['name'] == '文档.txt'])
        # 绕过文件对话框，直接指定目标目录
        task = lanshare_gui.GuiTask('download', '测试下载', total_files=1)
        gui.tasks.append(task)
        threading.Thread(target=gui._run_download, daemon=True,
                         args=(task, '', '本机',
                               [(gui._rel_of(sel[0]), sel[0])], dl_dir)).start()
        ok = pump_until(gui, lambda: task.status in ('done', 'error'), timeout=30)
        check('下载任务完成', ok and task.status == 'done',
              '%s %s' % (task.status, task.errors))
        out_file = os.path.join(dl_dir, '文档.txt')
        check('文件确实保存到了指定目录', os.path.isfile(out_file), dl_dir)
        if os.path.isfile(out_file):
            same = (open(out_file, 'rb').read() ==
                    open(os.path.join(src_dir, '文档.txt'), 'rb').read())
            check('下载内容与原文件一致', same)

        # ---- 6. 下载整个目录（含子目录）----
        dl2 = os.path.join(tmp, '下载目录2')
        os.makedirs(dl2)
        entry = [e for e in gui.entries if e['name'] == '子目录'][0]
        task2 = lanshare_gui.GuiTask('download', '测试下载目录', total_files=1)
        gui.tasks.append(task2)
        threading.Thread(target=gui._run_download, daemon=True,
                         args=(task2, '', '本机', [(gui._rel_of(entry), entry)], dl2)).start()
        pump_until(gui, lambda: task2.status in ('done', 'error'), timeout=30)
        check('下载文件夹任务完成', task2.status == 'done',
              '%s %s' % (task2.status, task2.errors))
        check('文件夹结构保留',
              os.path.isfile(os.path.join(dl2, '子目录', '深层.bin')),
              os.listdir(dl2) if os.path.isdir(dl2) else '无')

        # ---- 7. 下载目录被记住 ----
        check('下载目录已记录到配置', cfg.download_dir == dl_dir, cfg.download_dir)
        reloaded = Config(cfg_dir)
        reloaded.path = cfg_path
        reloaded.load()
        check('重新读取配置后仍记得下载目录', reloaded.download_dir == dl_dir,
              reloaded.download_dir)

        # ---- 8. 上传（往本机某目录复制文件）----
        up_dir = os.path.join(tmp, '上传目标')
        os.makedirs(up_dir)
        up_src = os.path.join(tmp, '待上传.txt')
        with open(up_src, 'w', encoding='utf-8') as f:
            f.write('上传测试')
        task3 = lanshare_gui.GuiTask('copy', '测试上传', total_files=1)
        gui.tasks.append(task3)
        threading.Thread(target=gui._run_local_copy, daemon=True,
                         args=(task3, [(up_src, lanshare_gui.abs_to_rel(
                             os.path.join(up_dir, '待上传.txt')), False)],
                               lanshare_gui.abs_to_rel(up_dir))).start()
        pump_until(gui, lambda: task3.status in ('done', 'error'), timeout=30)
        check('上传（复制）任务完成', task3.status == 'done',
              '%s %s' % (task3.status, task3.errors))
        check('文件出现在目标目录',
              os.path.isfile(os.path.join(up_dir, '待上传.txt')))

        # ---- 9. 托盘：隐藏与恢复 ----
        gui.show()
        gui.root.update()
        check('窗口可显示', gui.root.state() != 'withdrawn', gui.root.state())
        gui.hide()
        gui.root.update()
        check('隐藏后窗口收起（最小化到托盘）', gui.root.state() == 'withdrawn',
              gui.root.state())
        gui.show()
        gui.root.update()
        check('可以从托盘恢复窗口', gui.root.state() != 'withdrawn', gui.root.state())

        # ---- 10. 托盘对象能真正启动 ----
        tray = lanshare_gui.Tray(gui, state, cfg)
        started = tray.start()
        time.sleep(2)
        check('托盘图标成功启动', started and tray.icon is not None)
        if tray.icon is not None:
            check('托盘图标处于可见状态', bool(getattr(tray.icon, 'visible', False)))
            check('托盘提示文字正确',
                  'LanShare' in (getattr(tray.icon, 'title', '') or ''),
                  getattr(tray.icon, 'title', ''))

        # ---- 11. 开机自启开关 ----
        from lanshare_gui import set_autostart, autostart_enabled
        was = autostart_enabled()
        ok1, msg1 = set_autostart(True)
        check('可以开启开机自启', ok1 and autostart_enabled(), msg1)
        ok2, msg2 = set_autostart(False)
        check('可以关闭开机自启', ok2 and not autostart_enabled(), msg2)
        if was:
            set_autostart(True)      # 还原用户原本的设置
        check('自启命令包含 --tray（静默启动）',
              '--tray' in lanshare_gui.autostart_command(),
              lanshare_gui.autostart_command())

        # ---- 12. 图标资源 ----
        check('图标文件存在', os.path.exists(os.path.join(HERE, 'lanshare.ico')))
        from lanshare_icon import render_rgba
        px = render_rgba(16)
        check('图标能渲染且四角透明', len(px) == 16 * 16 * 4 and px[3] == 0)
    finally:
        try:
            if tray:
                tray.stop()
        except Exception:
            pass
        try:
            if gui:
                gui.root.destroy()
        except Exception:
            pass
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass

    print('\n' + '=' * 60)
    print('桌面界面测试：通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
    if FAIL:
        print('失败项：')
        for f in FAIL:
            print('  - ' + f)
    print('=' * 60)
    print('测试临时目录：%s（可手动删除）' % tmp)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
