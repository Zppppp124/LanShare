# -*- coding: utf-8 -*-
"""
把 lanshare.py + lanshare_ui.py 打包成真正的单文件版本：dist/LanShare.py

用法：python build_single.py
产物：dist/LanShare.py   —— 拷到任何装了 Python 3.8+ 的机器上直接运行
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, 'lanshare.py')
UI = os.path.join(HERE, 'lanshare_ui.py')
OUT_DIR = os.path.join(HERE, 'dist')
OUT = os.path.join(OUT_DIR, 'LanShare.py')

BANNER = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ==========================================================================
#  LanShare —— 局域网多设备文件互查 / 互传工具（单文件版，零第三方依赖）
#
#  本文件由 build_single.py 自动生成，请勿手工修改。
#  源码请改 lanshare.py 与 lanshare_ui.py，然后重新运行：python build_single.py
#
#  运行：python LanShare.py --root "D:\\共享" --open
# ==========================================================================
'''


def read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def main():
    main_src = read(MAIN)
    ui_src = read(UI)

    # 取出 UI 模块里的常量定义（去掉模块 docstring 与编码声明）
    m = re.search(r'FAVICON_SVG\s*=\s*.*', ui_src, re.S)
    if not m:
        print('错误：在 lanshare_ui.py 中找不到 FAVICON_SVG')
        return 1
    ui_body = m.group(0)

    # 去掉主程序末尾对 lanshare_ui 的导入，改为内嵌
    marker = 'from lanshare_ui import UI_HTML, FAVICON_SVG'
    if marker not in main_src:
        print('错误：在 lanshare.py 中找不到导入语句：%s' % marker)
        return 1
    main_src = main_src.replace(marker, '# 前端界面已内嵌在本文件末尾（由 build_single.py 生成）')
    # 顺带去掉那一行上面可能残留的注释行
    main_src = main_src.replace('# 内嵌前端（同目录 lanshare_ui.py；打包单文件版会把二者合并）\n', '')

    # 入口必须放在内嵌界面定义「之后」，否则启动时 UI_HTML 还未定义
    entry_re = re.compile(r"\nif __name__ == '__main__':\n(?:    .*\n?)+")
    em = entry_re.search(main_src)
    if not em:
        print("错误：在 lanshare.py 中找不到 __main__ 入口")
        return 1
    entry = em.group(0).strip('\n')
    main_src = main_src[:em.start()] + '\n' + main_src[em.end():]

    parts = [BANNER, main_src.rstrip(), '', '',
             '# ' + '=' * 74,
             '# 内嵌前端界面（由 lanshare_ui.py 生成）',
             '# ' + '=' * 74, '', ui_body.strip(), '', '', entry, '']
    out = '\n'.join(parts)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(out)

    # 自检：语法必须合法，且界面常量必须在入口之前定义
    import ast
    try:
        ast.parse(out)
    except SyntaxError as exc:
        print('错误：生成的文件语法不合法：%s' % exc)
        return 1
    i_ui = out.find('UI_HTML = ')
    i_entry = out.find("if __name__ == '__main__':")
    if i_ui < 0 or i_entry < 0 or i_ui > i_entry:
        print('错误：UI_HTML 定义必须在 __main__ 入口之前（ui@%d, entry@%d）' % (i_ui, i_entry))
        return 1
    if 'from lanshare_ui' in out:
        print('错误：单文件版仍依赖外部 lanshare_ui 模块')
        return 1

    size = os.path.getsize(OUT)
    print('已生成单文件版本：%s（%.1f KB，共 %d 行）' % (OUT, size / 1024.0, out.count('\n') + 1))
    print('自检通过：语法合法、界面已内嵌、无外部依赖、入口位置正确')
    print('可以直接运行：python "%s" --root "要共享的目录" --open' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
