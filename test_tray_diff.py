# -*- coding: utf-8 -*-
"""
严格验证托盘图标是否真的出现在通知区域。

方法：先在「LanShare 未运行」时截取托盘区，再在「运行后」截同一区域，
      对比像素差异。有差异才说明我们的图标（或它的位置）确实变了。
      这样不依赖肉眼判断，也不受其它程序图标干扰。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, 'dist', 'LanShare.exe')


def grab_tray(path):
    """截取主屏右下角托盘区域。"""
    from PIL import ImageGrab
    import ctypes
    u = ctypes.windll.user32
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    box = (max(0, sw - 500), max(0, sh - 60), sw, sh)
    img = ImageGrab.grab(bbox=box)
    img.save(path)
    return img


def diff_ratio(a, b):
    if a.size != b.size:
        return 1.0
    pa, pb = a.load(), b.load()
    w, h = a.size
    diff = 0
    for y in range(h):
        for x in range(w):
            ra, ga, ba = pa[x, y][:3]
            rb, gb, bb = pb[x, y][:3]
            if abs(ra - rb) + abs(ga - gb) + abs(ba - bb) > 40:
                diff += 1
    return diff / float(w * h)


def main():
    if not os.path.exists(EXE):
        print('找不到 EXE')
        return 1
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'ls-traydiff-%d' % int(time.time()))
    os.makedirs(tmp, exist_ok=True)
    shot_before = os.path.join(HERE, 'demo', 'tray-before.png')
    shot_after = os.path.join(HERE, 'demo', 'tray-after.png')
    os.makedirs(os.path.dirname(shot_before), exist_ok=True)

    # 确保没有残留进程
    subprocess.run(['taskkill', '/F', '/IM', 'LanShare.exe'],
                   capture_output=True, timeout=15)
    time.sleep(2)

    print('1) 截取「未运行」时的托盘区…')
    before = grab_tray(shot_before)

    print('2) 以 --tray 启动 LanShare…')
    proc = subprocess.Popen([EXE, '--tray', '--port', '8765', '--no-discovery',
                             '--config', os.path.join(tmp, 'c.json')], cwd=HERE)
    try:
        time.sleep(14)
        print('3) 再截一次托盘区…')
        after = grab_tray(shot_after)
        ratio = diff_ratio(before, after)
        print('\n   像素差异比例：%.2f%%' % (ratio * 100))
        # 一个 16x16 的图标在 500x60 区域里约占 0.85%；
        # 加上文字/提示变化，超过 0.3% 就足以判定「有东西出现了」
        ok = ratio > 0.003
        print('   判断：%s' % ('托盘图标确实出现了 ✅' if ok else '未检测到变化 ❌'))
        print('   对比图：%s / %s' % (shot_before, shot_after))

        # 顺便把两次截图各自裁一块放大存下来，方便人工核对
        try:
            from PIL import Image
            for tag, img in (('before', before), ('after', after)):
                crop = img.crop((img.size[0] - 260, 0, img.size[0], img.size[1]))
                crop = crop.resize((crop.size[0] * 2, crop.size[1] * 2), Image.NEAREST)
                crop.save(os.path.join(HERE, 'demo', 'tray-zoom-%s.png' % tag))
        except Exception as exc:
            print('   放大图失败:', exc)
        return 0 if ok else 1
    finally:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True, timeout=15)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
