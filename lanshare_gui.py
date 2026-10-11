# -*- coding: utf-8 -*-
"""
LanShare 桌面界面（tkinter）与系统托盘。

特点
  * 原生窗口，不是网页
  * 关闭窗口 = 最小化到右下角托盘，服务继续在后台跑
  * 下载时可选择保存到哪个文件夹，并记住上次的位置
  * 可开启「开机自动静默启动」（写注册表 Run 项）
  * 左侧列出局域网内其它电脑，直接浏览、下载、互传

线程约定：tkinter 只允许在主线程操作，所有后台线程的结果都通过队列
（self.queue）交给主线程的 _pump 处理。
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import ttk, filedialog, messagebox, font as tkfont

import lanshare
from lanshare import (
    APP_NAME, LocalDirectory, RemoteDirectory, RemoteError, __version__,
    build_payload, human_size, join_rel, list_drives, local_ips, log, norm_rel,
    run_transfer,
)

# ---------------------------------------------------------------------------
# 可爱字体
#
# 用随程序分发的中文圆体（站酷快乐体，OFL 许可，可免费商用）。
# 找不到就用系统里最接近的圆润字体兜底。
# ---------------------------------------------------------------------------

# 圆润但清楚：庆科黄油体（笔画更粗，比快乐体好认）
_CUTE_F_FILE = 'ZCOOLQingKeHuangYou-Regular.ttf'
_CUTE_F_FAMILY = 'ZCOOL QingKe HuangYou'
# 旧字体保留作兜底
_CUTE_FALLBACK_FILE = 'ZCOOLKuaiLe-Regular.ttf'
_CUTE_FALLBACK_FAMILY = 'ZCOOL KuaiLe'
_cute_family = None


def _font_path():
    here = os.path.dirname(os.path.abspath(__file__))
    # 打包成 exe 后资源解压在 sys._MEIPASS；开发时就在脚本同级的 assets 下
    roots = [getattr(sys, '_MEIPASS', None), here,
             os.path.join(here, 'assets')]
    for root in roots:
        if not root:
            continue
        for name in (_CUTE_F_FILE, 'assets/' + _CUTE_F_FILE,
                     _CUTE_FALLBACK_FILE, 'assets/' + _CUTE_FALLBACK_FILE):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
    return None


def load_cute_font():
    """把可爱的圆体注册进当前进程，返回字体族名。"""
    global _cute_family
    if _cute_family:
        return _cute_family
    path = _font_path()
    if path and os.name == 'nt':
        try:
            import ctypes
            gdi = ctypes.windll.gdi32
            gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint,
                                              ctypes.c_void_p]
            gdi.AddFontResourceExW.restype = ctypes.c_int
            # 10 = FR_PRIVATE，只对当前进程生效，不往系统里装字体
            if gdi.AddFontResourceExW(path, 0x10, None):
                base = os.path.basename(path)
                _cute_family = (_CUTE_F_FAMILY if base == _CUTE_F_FILE
                                else _CUTE_FALLBACK_FAMILY)
                log.info('已加载可爱字体：%s', _cute_family)
                return _cute_family
        except Exception as exc:
            log.debug('加载可爱字体失败：%s', exc)
    _cute_family = 'Microsoft YaHei UI'
    return _cute_family


FONT = 'Microsoft YaHei UI'      # 可爱显示字体，运行时替换
FONT_UI = 'Microsoft YaHei UI'   # 密集数据（文件列表）用更清楚的无衬线字体
MONO = 'Consolas'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'LanShare'

# ---------------------------------------------------------------------------
# 主题配色（淡紫）
#
# 全部集中在这里，想换色只改这一处。整体是「浅底 + 淡紫点缀」，
# 不做大面积深色，保证长时间看着不累。
# ---------------------------------------------------------------------------

THEME = {
    'bg':           '#F7F5FC',   # 窗口底色：极淡的紫
    'surface':      '#FFFFFF',   # 卡片
    'surface_alt':  '#FBFAFE',   # 斑马纹 / 次级面
    'header':       '#EDE7FB',   # 顶部标题带
    'sidebar':      '#F2EEFB',   # 左侧设备栏
    'border':       '#E4DEF4',   # 淡紫描边
    'primary':      '#8B7CD8',   # 主色：柔和紫
    'primary_dark': '#7566C4',
    'primary_soft': '#EDE7FB',   # 主色浅底（悬停）
    'selection':    '#DCD1F7',   # 选中行
    'text':         '#3B3552',   # 正文（紫灰）
    'muted':        '#8B85A3',   # 次要文字
    'ok':           '#3FA46A',
    'off':          '#B9B3C9',
    'danger':       '#C4556A',
}


def apply_theme(root):
    """把 ttk 控件统一成淡紫主题。

    用 clam 主题打底：vista/xpnative 会忽略大部分背景色设置，
    只有 clam 能让我们自由定义颜色。
    """
    global FONT, FONT_UI
    FONT = load_cute_font()      # 可爱圆体，加载不到时自动回落到雅黑
    FONT_UI = FONT               # 界面默认跟随可爱字体
    c = THEME
    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    try:
        root.configure(bg=c['bg'])
    except Exception:
        pass

    base_font = (FONT, 9)
    style.configure(
        '.', font=base_font, background=c['bg'], foreground=c['text'],
        bordercolor=c['border'], lightcolor=c['surface'],
        darkcolor=c['border'], focuscolor=c['primary_soft'],
    )

    # 容器
    style.configure('TFrame', background=c['bg'])
    style.configure('Card.TFrame', background=c['surface'])
    style.configure('Sidebar.TFrame', background=c['sidebar'])
    style.configure('Header.TFrame', background=c['header'])
    style.configure('Toolbar.TFrame', background=c['surface'])

    # 文本
    style.configure('TLabel', background=c['bg'], foreground=c['text'])
    style.configure('Card.TLabel', background=c['surface'], foreground=c['text'])
    style.configure('Sidebar.TLabel', background=c['sidebar'], foreground=c['text'])
    style.configure('Muted.TLabel', background=c['bg'], foreground=c['muted'])
    style.configure('CardMuted.TLabel', background=c['surface'], foreground=c['muted'])
    style.configure('Title.TLabel', background=c['header'],
                    foreground=c['primary_dark'], font=(FONT, 16, 'bold'))
    style.configure('Subtitle.TLabel', background=c['header'], foreground=c['muted'])
    style.configure('Section.TLabel', background=c['surface'],
                    foreground=c['text'], font=(FONT, 10, 'bold'))
    style.configure('SidebarSection.TLabel', background=c['sidebar'],
                    foreground=c['muted'], font=(FONT, 9, 'bold'))

    # 按钮
    style.configure(
        'TButton', font=base_font, padding=(10, 5),
        background=c['surface'], foreground=c['text'],
        bordercolor=c['border'], lightcolor=c['surface'], darkcolor=c['border'],
        relief='flat',
    )
    style.map(
        'TButton',
        background=[('disabled', c['surface_alt']), ('pressed', c['selection']),
                    ('active', c['primary_soft'])],
        foreground=[('disabled', c['muted'])],
        bordercolor=[('active', c['primary']), ('focus', c['primary'])],
    )
    style.configure(
        'Accent.TButton', font=(FONT, 9, 'bold'), padding=(12, 6),
        background=c['primary'], foreground='#FFFFFF',
        bordercolor=c['primary'], lightcolor=c['primary'], darkcolor=c['primary'],
        relief='flat',
    )
    style.map(
        'Accent.TButton',
        background=[('disabled', c['off']), ('pressed', c['primary_dark']),
                    ('active', c['primary_dark'])],
        foreground=[('disabled', '#FFFFFF')],
        bordercolor=[('pressed', c['primary_dark']), ('active', c['primary_dark'])],
    )
    style.configure(
        'Ghost.TButton', font=base_font, padding=(8, 5),
        background=c['header'], foreground=c['primary_dark'],
        bordercolor=c['header'], lightcolor=c['header'], darkcolor=c['header'],
        relief='flat',
    )
    style.map(
        'Ghost.TButton',
        background=[('active', c['primary_soft']), ('pressed', c['selection'])],
        bordercolor=[('active', c['primary_soft'])],
    )

    # 输入框
    style.configure(
        'TEntry', fieldbackground=c['surface'], foreground=c['text'],
        bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'],
        insertcolor=c['primary'], padding=4,
    )
    style.map('TEntry',
              bordercolor=[('focus', c['primary'])],
              lightcolor=[('focus', c['primary'])],
              darkcolor=[('focus', c['primary'])])
    style.configure('TCombobox', fieldbackground=c['surface'], background=c['surface'],
                    foreground=c['text'], bordercolor=c['border'], arrowcolor=c['primary_dark'])

    # 勾选 / 单选
    style.configure('TCheckbutton', background=c['bg'], foreground=c['text'],
                    indicatorcolor=c['surface'], focuscolor=c['primary_soft'])
    style.map('TCheckbutton',
              background=[('active', c['bg'])],
              indicatorcolor=[('selected', c['primary']), ('active', c['primary_soft'])])
    style.configure('Card.TCheckbutton', background=c['surface'], foreground=c['text'],
                    indicatorcolor=c['surface'])
    style.map('Card.TCheckbutton',
              background=[('active', c['surface'])],
              indicatorcolor=[('selected', c['primary']), ('active', c['primary_soft'])])
    style.configure('TRadiobutton', background=c['surface'], foreground=c['text'],
                    indicatorcolor=c['surface'])
    style.map('TRadiobutton',
              background=[('active', c['surface'])],
              indicatorcolor=[('selected', c['primary']), ('active', c['primary_soft'])])

    # 文件列表
    style.configure(
        'Treeview',
        # 文件名列密集、还有大量数字，用更清楚的无衬线字体，避免看不清
        font=('Microsoft YaHei UI', 10),
        rowheight=32,
        background=c['surface'],
        fieldbackground=c['surface'],
        foreground=c['text'],
        bordercolor=c['border'],
        lightcolor=c['surface'],
        darkcolor=c['surface'],
    )
    style.map(
        'Treeview',
        background=[('selected', c['selection'])],
        foreground=[('selected', c['text'])],
    )
    style.configure(
        'Treeview.Heading',
        # 表头也是密集小字，用更清楚的无衬线粗体
        font=('Microsoft YaHei UI', 10, 'bold'),
        background=c['header'],
        foreground=c['primary_dark'],
        relief='flat',
        padding=(8, 6),
        bordercolor=c['border'],
        lightcolor=c['header'],
        darkcolor=c['header'],
    )
    style.map(
        'Treeview.Heading',
        background=[('active', c['primary_soft'])],
        foreground=[('active', c['primary_dark'])],
    )
    style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])

    # 滚动条
    style.configure(
        'Vertical.TScrollbar',
        background=c['header'], troughcolor=c['bg'],
        bordercolor=c['bg'], arrowcolor=c['primary_dark'],
        lightcolor=c['header'], darkcolor=c['header'], relief='flat',
    )
    style.map('Vertical.TScrollbar',
              background=[('active', c['selection']), ('pressed', c['primary'])])

    # 进度条 / 分隔线 / 面板
    style.configure('TProgressbar', background=c['primary'], troughcolor=c['header'],
                    bordercolor=c['header'], lightcolor=c['primary'], darkcolor=c['primary'])
    style.configure('TSeparator', background=c['border'])
    style.configure('TPanedwindow', background=c['bg'])
    style.configure('Sash', background=c['border'])
    return style


def kind_of(entry):
    """给条目分类，用于图标和「类型」列。"""
    if entry.get('drive'):
        return 'drive'
    if entry.get('is_dir'):
        return 'dir'
    ext = (entry.get('ext') or '').lower()
    groups = (
        ('image', ('jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tif', 'tiff')),
        ('video', ('mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v', 'mpg', 'mpeg')),
        ('audio', ('mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg', 'wma', 'ape')),
        ('archive', ('zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'iso')),
        ('exe', ('exe', 'msi', 'bat', 'cmd', 'apk', 'sh')),
        ('code', ('py', 'js', 'ts', 'c', 'h', 'cpp', 'cs', 'java', 'go', 'rs',
                  'html', 'css', 'json', 'xml', 'yml', 'yaml', 'ini', 'cfg', 'toml')),
        ('doc', ('doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf')),
        ('text', ('txt', 'md', 'log')),
    )
    for name, exts in groups:
        if ext in exts:
            return name
    return 'file'


ICONS = {
    'drive':   '💽',
    'dir':     '📁',
    'image':   '🖼',
    'video':   '🎬',
    'audio':   '🎵',
    'archive': '🗜',
    'exe':     '⚙',
    'code':    '📜',
    'doc':     '📘',
    'text':    '📄',
    'file':    '📦',
}

TYPE_NAMES = {
    'drive': '磁盘', 'dir': '文件夹', 'image': '图片', 'video': '视频',
    'audio': '音频', 'archive': '压缩包', 'exe': '程序', 'code': '代码',
    'doc': '文档', 'text': '文本', 'file': '文件',
}


def icon_for(entry):
    return ICONS.get(kind_of(entry), ICONS['file'])


def type_text(entry):
    """「类型」列显示的文字。"""
    kind = kind_of(entry)
    if kind == 'drive':
        return '磁盘'
    if kind == 'dir':
        return '文件夹'
    ext = (entry.get('ext') or '').upper()
    label = TYPE_NAMES.get(kind, '文件')
    return ('%s %s' % (ext, label)).strip() if ext else label


# ---------------------------------------------------------------------------
# 可爱气泡对话框
#
# 不用系统的 messagebox（那是方头方脑的对话框），统一换成圆润的对话气泡：
# 一圈淡紫描边 + 圆角 + 小尾巴，配可爱字体和大号表情图标。
# ---------------------------------------------------------------------------

BUBBLE_BG = '#FFFDFF'
BUBBLE_EDGE = '#E4DEF4'
# 被设为「透明色」的键色，画布上这个颜色会镂空，从而得到真正的圆角
BUBBLE_KEY = '#FFFCF8'


# ---------------------------------------------------------------------------
# 圆角按钮 + 矢量图标
#
# ttk 的按钮在 clam 主题下只能是方角，这里自己用画布画圆角，
# 视觉上更软更可爱，同时支持悬停 / 按下 / 禁用几种状态。
# ---------------------------------------------------------------------------

def _draw_round_rect(cv, x0, y0, x1, y1, r, **kw):
    """在画布上画一个圆角矩形（用平滑多边形近似）。"""
    pts = [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class RoundButton(tk.Canvas):
    """圆角按钮。文字里出现的 :plus: / :minus: / :chevron: 会画成矢量小图标。"""

    # 图标记号 -> 画法
    GLYPHS = (':plus:', ':minus:', ':chevron:')

    def __init__(self, parent, text='', command=None, kind='normal',
                 bg=None, width=None, height=36, radius=None, font=None,
                 pad_x=16):
        self.kind = kind                 # normal / accent / ghost / danger
        self.command = command
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._font = font or (FONT, 11)
        self._pad_x = pad_x
        self._radius = radius if radius is not None else height // 2
        self._bg = bg or THEME['surface']
        self._raw_text = text
        self._glyph = None
        self._label = text
        for g in self.GLYPHS:
            if text.startswith(g):
                self._glyph = g
                self._label = text[len(g):].strip()
                break

        if width is None:
            tw = 0
            if self._label:
                f = tkfont.Font(family=self._font[0], size=self._font[1])
                tw = f.measure(self._label)
            # 多留一点余量，避免字体测量略有偏差时把文字挤成两行
            width = tw + (20 if self._glyph else 0) + self._pad_x * 2 + 10
        tk.Canvas.__init__(self, parent, width=width,
                           height=self._radius * 2 + 2,
                           highlightthickness=0, bd=0, bg=self._bg)
        self._width = width
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<ButtonRelease-1>', self._on_release)
        self._redraw()

    def _palette(self):
        c = THEME
        if not self._enabled:
            return dict(bg=c['surface_alt'], fg=c['muted'], edge=c['border'])
        if self.kind == 'accent':
            base = dict(bg=c['primary'], fg='#FFFFFF', edge=c['primary'])
            if self._pressed:
                base.update(bg=c['primary_dark'], edge=c['primary_dark'])
            elif self._hover:
                base.update(bg=c['primary_dark'], edge=c['primary_dark'])
            return base
        if self.kind == 'ghost':
            base = dict(bg=self._bg, fg=c['primary_dark'], edge=self._bg)
            if self._pressed:
                base.update(bg=c['selection'], edge=c['selection'])
            elif self._hover:
                base.update(bg=c['primary_soft'], edge=c['primary_soft'])
            return base
        if self.kind == 'danger':
            base = dict(bg=c['surface'], fg=c['danger'], edge=c['border'])
            if self._pressed:
                base.update(bg='#F7DDE3', edge='#E9BFC9')
            elif self._hover:
                base.update(bg='#FBEEF1', edge='#E9BFC9')
            return base
        base = dict(bg=c['surface'], fg=c['text'], edge=c['border'])
        if self._pressed:
            base.update(bg=c['selection'], edge=c['primary'])
        elif self._hover:
            base.update(bg=c['primary_soft'], edge=c['primary'])
        return base

    def _redraw(self):
        self.delete('all')
        pal = self._palette()
        w = self._width
        h = self._radius * 2
        self.configure(bg=self._bg)
        pad = 2
        _draw_round_rect(self, pad, pad, w - pad, h - pad,
                         max(6, self._radius - pad),
                         fill=pal['bg'], outline=pal['edge'])
        # 内容：图标 + 文字，整体居中
        cx = w // 2
        if self._glyph:
            f = tkfont.Font(family=self._font[0], size=self._font[1])
            tw = f.measure(self._label) if self._label else 0
            total = tw + (16 if self._label else 0)
            gx = cx - total // 2 + 7
            self._draw_glyph(self._glyph, gx, h // 2, pal['fg'])
            if self._label:
                self.create_text(gx + 15, h // 2, text=self._label, anchor='w',
                                 fill=pal['fg'], font=self._font)
        elif self._label:
            self.create_text(cx, h // 2, text=self._label, fill=pal['fg'],
                             font=self._font, width=w - 8)

    def _draw_glyph(self, glyph, x, y, color):
        if glyph == ':plus:':
            self.create_line(x - 5, y, x + 5, y, fill=color, width=2,
                             capstyle='round')
            self.create_line(x, y - 5, x, y + 5, fill=color, width=2,
                             capstyle='round')
        elif glyph == ':minus:':
            self.create_line(x - 5, y, x + 5, y, fill=color, width=2,
                             capstyle='round')
        elif glyph == ':chevron:':
            self.create_line(x - 4, y - 5, x + 2, y, fill=color, width=2,
                             capstyle='round', joinstyle='round')
            self.create_line(x + 2, y, x - 4, y + 5, fill=color, width=2,
                             capstyle='round', joinstyle='round')

    # ---- 交互 ----
    def _on_enter(self, _=None):
        if self._enabled:
            self._hover = True
            self._redraw()
            self.config(cursor='hand2')

    def _on_leave(self, _=None):
        self._hover = False
        self._pressed = False
        self._redraw()
        self.config(cursor='')

    def _on_press(self, _=None):
        if self._enabled:
            self._pressed = True
            self._redraw()

    def _on_release(self, event=None):
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._enabled and self.command:
            inside = (0 <= event.x <= int(self['width'])
                      and 0 <= event.y <= self._radius * 2)
            if inside:
                self.command()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.config(cursor='hand2' if self._enabled else '')
        self._redraw()

    def set_text(self, text):
        self._raw_text = text
        self._glyph = None
        self._label = text
        for g in self.GLYPHS:
            if text.startswith(g):
                self._glyph = g
                self._label = text[len(g):].strip()
                break
        self._redraw()

# 每种提示配一个表情和小标题，让弹窗有点性格
MOOD = {
    'info':    ('🐰', '提示'),
    'ok':      ('🌸', '好啦'),
    'warn':    ('🍮', '注意'),
    'error':   ('💧', '出错了'),
    'ask':     ('🐣', '确认一下'),
    'prompt':  ('✏️', '写点什么'),
}


class Bubble(tk.Toplevel):
    """圆角对话气泡。

    主体是别在画布上的 tk.Frame；圆角用「透明画布 + 带-key色的位图遮罩」
    画出来，这样四个角是真的透明，不会出现方形白边。
    """

    def __init__(self, parent, title='提示', message='', mood='info',
                 buttons=(), width=430, accent=None):
        tk.Toplevel.__init__(self, parent)
        self.withdraw()
        self.transient(parent)
        self.resizable(False, False)
        self.overrideredirect(True)
        # 用近白底色，再把和它同色的地方抠成透明，四角就是圆的了
        self.configure(bg=BUBBLE_KEY)
        self.result = None
        self._accent = accent or THEME['primary']
        self._transparent = False
        try:
            self.attributes('-transparentcolor', BUBBLE_KEY)
            self._transparent = True
        except Exception:
            self._transparent = False

        icon, default_title = MOOD.get(mood, MOOD['info'])
        title = title or default_title
        wrap = max(220, width - 96)

        self.canvas = tk.Canvas(self, bg=BUBBLE_KEY, highlightthickness=0,
                                bd=0, width=width, height=10)
        self.canvas.pack(fill='both', expand=True)

        body = tk.Frame(self.canvas, bg=BUBBLE_BG)
        self._body = body

        head = tk.Frame(body, bg=BUBBLE_BG)
        head.pack(fill='x', padx=22, pady=(18, 2))
        tk.Label(head, text=icon, bg=BUBBLE_BG, font=(FONT, 22)).pack(side='left')
        tk.Label(head, text=title, bg=BUBBLE_BG, fg=THEME['primary_dark'],
                 font=(FONT, 14, 'bold')).pack(side='left', padx=(9, 0))

        self.msg = tk.Label(body, text=message, bg=BUBBLE_BG, fg=THEME['text'],
                            font=(FONT, 12), justify='left', wraplength=wrap,
                            anchor='w')
        self.msg.pack(fill='x', padx=22, pady=(10, 4))

        # 附加内容（例如输入框）挂在这里
        self.extra = tk.Frame(body, bg=BUBBLE_BG)
        self.extra.pack(fill='x', padx=22)

        foot = tk.Frame(body, bg=BUBBLE_BG)
        foot.pack(fill='x', padx=20, pady=(12, 20))
        buttons = list(buttons) or [('好哒', 'ok', True)]
        for text, value, *rest in reversed(buttons):
            primary = bool(rest and rest[0])
            b = RoundButton(foot, text=text,
                            command=lambda v=value: self._finish(v),
                            kind='accent' if primary else 'normal',
                            bg=BUBBLE_BG, font=(FONT, 11), pad_x=20)
            b.pack(side='right', padx=(8, 0))

        self.bind('<Escape>', lambda e: self._finish(None))
        self.bind('<Return>', lambda e: self._finish(self._default_value(buttons)))
        self._place()

    def _default_value(self, buttons):
        for text, value, *rest in buttons:
            if rest and rest[0]:
                return value
        return buttons[0][1] if buttons else 'ok'

    def _finish(self, value):
        self.result = value
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def set_message(self, text):
        self.msg.config(text=text)
        self._place()

    def _place(self):
        """按内容算出气泡尺寸，画上圆角和小尾巴，并居中到主窗口。"""
        self.update_idletasks()
        self.canvas.delete('all')
        self.extra.update_idletasks()
        w = max(320, self.canvas.winfo_reqwidth())
        h = self._body.winfo_reqheight()
        tail = 16
        pad = 2                      # 给描边留一点空间
        total_w = w + pad * 2
        total_h = h + tail + pad * 2
        self.canvas.config(width=total_w, height=total_h)
        self.geometry('%dx%d' % (total_w, total_h))

        x0, y0 = pad, pad
        x1, y1 = pad + w, pad + h
        r = 20
        outline = BUBBLE_EDGE
        # 先画身体（填充 + 描边）
        self._shape(x0, y0, x1, y1, r, tail)
        # 再把内容贴上去；内容框比气泡内收 1px，免得压住描边
        self.canvas.create_window(x0 + 1, y0 + 1, window=self._body,
                                  anchor='nw', width=w - 2)

        self.update_idletasks()
        pw = self.master.winfo_width()
        ph = self.master.winfo_height()
        px = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        if pw < 50:
            px, py, pw, ph = 200, 200, 400, 300
        x = px + max(0, (pw - total_w) // 2)
        y = py + max(20, (ph - total_h) // 2)
        self.geometry('+%d+%d' % (x, y))
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except Exception:
            pass

    def _shape(self, x0, y0, x1, y1, r, tail):
        """画圆角气泡 + 底部小尾巴（同色描边，衔接处不留缝）。"""
        # 圆角矩形：四角用平滑多边形近似
        pts = [
            x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
        ]
        self.canvas.create_polygon(pts, smooth=True, splinesteps=24,
                                   fill=BUBBLE_BG, outline=BUBBLE_EDGE, width=1.5)
        # 小尾巴：先铺一层同色的底盖住底边，再画两个三角拼出侧面描边
        tx = x0 + int((x1 - x0) * 0.30)
        tw = 30
        # 覆盖底边描边，让尾巴和身体连成一体
        self.canvas.create_rectangle(tx + 2, y1 - 3, tx + tw - 2, y1 + 1,
                                     fill=BUBBLE_BG, outline='')
        self.canvas.create_polygon(
            tx, y1 - 1, tx + 7, y1 + tail, tx + tw, y1 - 1,
            fill=BUBBLE_BG, outline='')
        # 两侧描边
        self.canvas.create_line(tx, y1 - 1, tx + 7, y1 + tail,
                                fill=BUBBLE_EDGE, width=1.5)
        self.canvas.create_line(tx + 7, y1 + tail, tx + tw, y1 - 1,
                                fill=BUBBLE_EDGE, width=1.5)


def _parent_root(parent):
    """找到可用于居中定位的主窗口。"""
    if isinstance(parent, tk.Misc):
        return parent
    return tk._default_root


def bubble(parent, message, title='', mood='info', buttons=None, width=380):
    """弹一个可爱气泡，返回用户点的值。"""
    root = _parent_root(parent)
    if root is None:
        return None
    dlg = Bubble(root, title=title, message=message, mood=mood,
                 buttons=buttons, width=width)
    root.wait_window(dlg)
    return dlg.result


def bubble_info(parent, message, title=''):
    return bubble(parent, message, title=title, mood='info')


def bubble_ok(parent, message, title=''):
    return bubble(parent, message, title=title, mood='ok')


def bubble_warn(parent, message, title=''):
    return bubble(parent, message, title=title, mood='warn',
                  buttons=[('知道啦', 'ok', True)])


def bubble_error(parent, message, title=''):
    return bubble(parent, message, title=title, mood='error',
                  buttons=[('好嘛', 'ok', True)])


def bubble_ask(parent, message, title='', yes='好哒', no='算了'):
    return bubble(parent, message, title=title, mood='ask',
                  buttons=[(no, False), (yes, True, True)]) is True


class _InputBubble(Bubble):
    """带输入框的气泡，用于新建文件夹 / 重命名。"""

    def __init__(self, parent, title, label, initial=''):
        Bubble.__init__(self, parent, title=title, message=label, mood='prompt',
                        buttons=[('取消', None), ('好哒', 'ok', True)])
        self.var = tk.StringVar(value=initial)
        self.entry = tk.Entry(self.extra, textvariable=self.var,
                              font=(FONT, 12), bg='#FFFFFF',
                              fg=THEME['text'], relief='flat',
                              highlightthickness=2,
                              highlightbackground=THEME['border'],
                              highlightcolor=THEME['primary'],
                              insertbackground=THEME['primary'])
        self.entry.pack(fill='x', pady=(6, 2), ipady=5)
        self.entry.focus_set()
        self.entry.select_range(0, 'end')
        self.bind('<Return>', lambda e: self._finish('ok'))
        self._place()

    def _finish(self, value):
        self.result = value
        Bubble._finish(self, value)


def bubble_prompt(parent, title, label, initial=''):
    """可爱风单行输入，返回输入内容或 None。"""
    root = _parent_root(parent)
    if root is None:
        return None
    dlg = _InputBubble(root, title, label, initial)
    root.wait_window(dlg)
    if dlg.result != 'ok':
        return None
    return dlg.var.get().strip()


def bubble_toast(parent, message, msec=2200):
    """右下角悄悄冒出来的小气泡，不挡操作。"""
    root = _parent_root(parent)
    if root is None:
        return
    tip = tk.Toplevel(root)
    tip.withdraw()
    tip.overrideredirect(True)
    tip.configure(bg=BUBBLE_EDGE)
    frm = tk.Frame(tip, bg=BUBBLE_BG)
    frm.pack(fill='both', expand=True, padx=1, pady=1)
    tk.Label(frm, text='💜  ' + message, bg=BUBBLE_BG, fg=THEME['primary_dark'],
             font=(FONT, 11), padx=16, pady=10).pack()
    tip.update_idletasks()
    w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
    x = root.winfo_rootx() + max(0, root.winfo_width() - w - 24)
    y = root.winfo_rooty() + max(0, root.winfo_height() - h - 24)
    if root.winfo_width() < 50:
        x, y = 220, 220
    tip.geometry('%dx%d+%d+%d' % (w, h, x, y))
    tip.deiconify()
    tip.lift()
    tip.after(msec, tip.destroy)
    return tip


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def abs_to_rel(path):
    """把本机绝对路径转成全盘模式下的相对路径（D:\\下载 -> D:/下载）。"""
    return norm_rel(str(path).replace('\\', '/'))


def rel_to_abs(rel):
    """全盘模式相对路径 -> 本机绝对路径。"""
    rel = norm_rel(rel)
    if not rel:
        return ''
    if len(rel) >= 2 and rel[1] == ':':
        return os.path.abspath(rel.replace('/', os.sep))
    return os.path.abspath(rel.replace('/', os.sep))


def fmt_time(ts):
    if not ts:
        return ''
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# 开机自启（注册表）
# ---------------------------------------------------------------------------

def autostart_supported():
    return os.name == 'nt'


def autostart_command():
    """开机自启要执行的命令行。"""
    if getattr(sys, 'frozen', False):
        return '"%s" --tray' % os.path.abspath(sys.executable)
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), 'lanshare.py'))
    return '"%s" "%s" --tray' % (sys.executable, script)


def autostart_enabled():
    if not autostart_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, RUN_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enable):
    """开关开机自启，返回 (成功, 说明)。"""
    if not autostart_supported():
        return False, '这个系统不支持'
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
                return True, '已开启：开机后会自动在后台运行（右下角托盘）'
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
            return True, '已关闭开机自动启动'
    except Exception as exc:
        return False, '设置失败：%s' % exc


# ---------------------------------------------------------------------------
# 传输任务（GUI 用）
# ---------------------------------------------------------------------------

class GuiTask(object):
    """
    一个 GUI 传输任务。

    它刻意实现了「字典式」访问（get / __getitem__ / __setitem__），因为
    lanshare.run_transfer() 是拿字典来记录进度的。这样传给它的是任务本身
    而不是副本，界面上的「取消 / 暂停」才能立刻生效，进度也能直接读。
    """

    _FIELDS = ('cancel', 'pause', 'sent_bytes', 'done_files', 'total_bytes',
               'total_files', 'current', 'speed', 'progress')

    def __init__(self, kind, title, total_bytes=0, total_files=0):
        self.kind = kind
        self.title = title
        self.total_bytes = total_bytes
        self.total_files = total_files
        self.sent_bytes = 0
        self.done_files = 0
        self.status = 'running'
        self.message = ''
        self.errors = []
        self.cancel = False
        self.pause = False
        self.current = ''
        self.speed = 0
        self.progress = 0.0
        self.started = time.time()
        self.finished = None

    # ---- 字典式访问（供 run_transfer 使用）----
    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __contains__(self, key):
        return hasattr(self, key)

    def keys(self):
        return list(self._FIELDS)

    def __repr__(self):
        return '<GuiTask %s %s %.1f%%>' % (self.kind, self.status, self.progress)

    @property
    def speed_now(self):
        dt = (self.finished or time.time()) - self.started
        return self.sent_bytes / dt if dt > 0.5 else 0

    def calc_progress(self):
        """给界面显示用的进度（run_transfer 只会在有总量时写 progress）。"""
        if self.status == 'done':
            return 100.0
        if self.progress:
            return self.progress
        if self.total_bytes:
            return min(100.0, self.sent_bytes * 100.0 / self.total_bytes)
        if self.total_files:
            return min(100.0, self.done_files * 100.0 / self.total_files)
        return 0.0


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class MainWindow(object):

    def __init__(self, state, config, services, on_quit):
        self.state = state
        self.cfg = config
        self.services = services          # dict: httpd / discovery / peer_keeper
        self.on_quit = on_quit
        self.queue = queue.Queue()
        self.tasks = []
        self.visible = False
        self._closing_to_tray = False
        self.cur = {'dev': '', 'path': ''}     # 当前浏览位置
        self.entries = []
        self.devices = []
        self.last_download_dir = (self.cfg.download_dir or '')

        self._build_root()
        self._build_ui()
        self.root.protocol('WM_DELETE_WINDOW', self.hide)
        self.refresh_devices()
        self.navigate('')
        self._pump()

    # ---------------- 窗口骨架 ----------------
    def _build_root(self):
        self.root = tk.Tk()
        self.root.title('%s %s —— 局域网文件互传' % (APP_NAME, __version__))
        self.root.geometry('1180x720')
        self.root.minsize(940, 580)
        self.style = apply_theme(self.root)
        try:
            ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lanshare.ico')
            if os.path.exists(ico):
                self.root.iconbitmap(ico)
        except Exception:
            pass
        self._selected_iid = None

    # ---------------- 小部件工厂 ----------------
    def _card(self, parent, padding=12, style='Card.TFrame'):
        """带 1px 淡紫描边的卡片容器。"""
        c = THEME
        outer = tk.Frame(parent, bg=c['border'], bd=0, highlightthickness=0)
        inner = tk.Frame(outer, bg=c['surface'], bd=0, highlightthickness=0)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        wrap = ttk.Frame(inner, style=style, padding=padding)
        wrap.pack(fill='both', expand=True)
        return outer, wrap

    def _nav_button(self, parent, text, command, accent=False):
        return ttk.Button(parent, text=text, command=command,
                          style='Accent.TButton' if accent else 'TButton')

    def _round_button(self, parent, text, command, kind='normal', bg=None,
                      width=None, height=36, font=None, pad_x=16):
        """圆角可爱按钮。text 以 :plus: / :minus: / :chevron: 开头会画成图标。"""
        if font is None:
            font = (FONT, 11)
        return RoundButton(parent, text=text, command=command, kind=kind,
                           bg=bg, width=width, height=height, font=font,
                           pad_x=pad_x)

    def _build_ui(self):
        c = THEME

        # 顶部标题带
        header = ttk.Frame(self.root, style='Header.TFrame', padding=(18, 12, 18, 12))
        header.pack(fill='x')
        ttk.Label(header, text='LanShare', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='局域网文件互传', style='Subtitle.TLabel').pack(
            side='left', padx=(10, 0), pady=(6, 0))
        self.lbl_self = ttk.Label(header, text='', style='Subtitle.TLabel')
        self.lbl_self.pack(side='left', padx=18, pady=(6, 0))
        self._round_button(header, '设置', self.open_settings, kind='accent',
                           bg=c['header'], font=(FONT, 11)).pack(side='right')
        self._round_button(header, '传输记录', self.open_transfers,
                           bg=c['header'], font=(FONT, 11)).pack(side='right', padx=8)
        self._round_button(header, '挂载磁盘', self.mount_dialog,
                           bg=c['header'], font=(FONT, 11)).pack(side='right', padx=8)
        self._round_button(header, '刷新设备', self.refresh_devices,
                           bg=c['header'], font=(FONT, 11)).pack(side='right')

        # 主体：左侧设备卡片 + 右侧文件卡片
        body = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=0, minsize=286)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ---- 左：设备列表 ----
        left_outer, left = self._card(body, padding=12, style='Sidebar.TFrame')
        left_outer.grid(row=0, column=0, sticky='nsw', padx=(0, 12))
        ttk.Label(left, text='局域网设备', style='SidebarSection.TLabel').pack(
            anchor='w', pady=(0, 8))
        list_wrap = tk.Frame(left, bg=c['border'], bd=0, highlightthickness=0)
        list_wrap.pack(fill='both', expand=True)
        self.dev_list = tk.Listbox(
            list_wrap, font=(FONT, 10), activestyle='none',
            exportselection=False, selectmode='browse',
            bg=c['surface'], fg=c['text'], relief='flat', bd=0,
            highlightthickness=0, selectbackground=c['selection'],
            selectforeground=c['text'], selectborderwidth=0,
            cursor='hand2', width=34, height=12,
        )
        self.dev_list.pack(fill='both', expand=True, padx=1, pady=1)
        self.dev_list.bind('<<ListboxSelect>>', lambda e: self._on_pick_device())
        self._round_button(left, ':plus: 添加设备 IP', self.add_device_dialog,
                           bg=c['sidebar'], font=(FONT, 11)).pack(
            fill='x', pady=(10, 0))

        # ---- 右：文件浏览 ----
        right_outer, right = self._card(body, padding=0)
        right_outer.grid(row=0, column=1, sticky='nsew')

        # 工具栏
        bar = ttk.Frame(right, style='Toolbar.TFrame', padding=(10, 10, 10, 8))
        bar.pack(fill='x')
        top_bar = ttk.Frame(bar, style='Toolbar.TFrame')
        top_bar.pack(fill='x')
        self._round_button(top_bar, ':chevron: 返回上级', self.go_up,
                           font=(FONT, 11)).pack(side='left')
        self._round_button(top_bar, '刷新',
                           lambda: self.navigate(self.cur['path']),
                           font=(FONT, 11)).pack(side='left', padx=(8, 0))
        self._round_button(top_bar, '上传文件', self.upload_files, kind='accent',
                           font=(FONT, 11)).pack(side='left', padx=(16, 0))
        self._round_button(top_bar, '上传文件夹', self.upload_folder,
                           font=(FONT, 11)).pack(side='left', padx=8)
        self._round_button(top_bar, '发送到设备…', self.send_to_device,
                           font=(FONT, 11)).pack(side='left')
        self._round_button(top_bar, '下载到…', self.download_selected,
                           font=(FONT, 11)).pack(side='left', padx=8)

        sub_bar = ttk.Frame(bar, style='Toolbar.TFrame')
        sub_bar.pack(fill='x', pady=(8, 0))
        self.lbl_actions = ttk.Label(sub_bar, text='对选中项：', style='CardMuted.TLabel')
        self.lbl_actions.pack(side='left')
        self._round_button(sub_bar, ':plus: 新建文件夹', self.new_folder,
                           font=(FONT, 11)).pack(side='left', padx=(6, 0))
        self._round_button(sub_bar, '重命名', self.rename_selected,
                           font=(FONT, 11)).pack(side='left', padx=8)
        self._round_button(sub_bar, '删除', self.delete_selected, kind='danger',
                           font=(FONT, 11)).pack(side='left')

        ttk.Separator(right, orient='horizontal').pack(fill='x')

        # 路径栏
        crumb = ttk.Frame(right, style='Toolbar.TFrame', padding=(12, 8, 12, 8))
        crumb.pack(fill='x')
        self.lbl_path = ttk.Label(crumb, text='', style='CardMuted.TLabel')
        self.lbl_path.pack(side='left')
        self.var_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(crumb, text='显示隐藏项', variable=self.var_hidden,
                        style='Card.TCheckbutton',
                        command=lambda: self.navigate(self.cur['path'])).pack(side='right')

        ttk.Separator(right, orient='horizontal').pack(fill='x')

        # 文件列表
        wrap = ttk.Frame(right, style='Card.TFrame', padding=(1, 1, 1, 1))
        wrap.pack(fill='both', expand=True, padx=10, pady=(8, 10))
        cols = ('name', 'type', 'size', 'mtime')
        self.tree = ttk.Treeview(wrap, columns=cols, show='headings',
                                 selectmode='extended')
        self.tree.heading('#0', text='')
        self.tree.heading('name', text='名称', anchor='w')
        self.tree.heading('type', text='类型', anchor='w')
        self.tree.heading('size', text='大小', anchor='e')
        self.tree.heading('mtime', text='修改时间', anchor='w')
        self.tree.column('#0', width=0, minwidth=0, stretch=False)
        self.tree.column('name', width=420, minwidth=200, anchor='w', stretch=True)
        self.tree.column('type', width=120, minwidth=90, anchor='w', stretch=False)
        self.tree.column('size', width=130, minwidth=90, anchor='e', stretch=False)
        self.tree.column('mtime', width=160, minwidth=120, anchor='w', stretch=False)
        self.tree.tag_configure('odd', background=c['surface_alt'])
        self.tree.tag_configure('even', background=c['surface'])
        self.tree.tag_configure('dir', foreground=c['primary_dark'])
        self.tree.tag_configure('sel', background=c['selection'])
        vs = ttk.Scrollbar(wrap, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', self._on_double)
        self.tree.bind('<Return>', self._on_double)
        self.tree.bind('<Button-3>', self._on_right_click)
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)

        # 底部状态栏
        bottom = ttk.Frame(self.root, style='Header.TFrame', padding=(16, 6, 16, 6))
        bottom.pack(fill='x')
        self.lbl_status = ttk.Label(bottom, text='就绪', style='Subtitle.TLabel')
        self.lbl_status.pack(side='left')
        self.prog = ttk.Progressbar(bottom, length=240, mode='determinate')
        self.prog.pack(side='right')
        self.lbl_pct = ttk.Label(bottom, text='', width=22, anchor='e',
                                 style='Subtitle.TLabel')
        self.lbl_pct.pack(side='right', padx=8)

        # 右键菜单
        self.menu = tk.Menu(self.root, tearoff=0, font=(FONT, 9),
                            bg=c['surface'], fg=c['text'],
                            activebackground=c['primary_soft'],
                            activeforeground=c['primary_dark'],
                            bd=0, relief='flat')
        self.menu.add_command(label='打开 / 进入', command=self.open_selected)
        self.menu.add_command(label='下载到…', command=self.download_selected)
        self.menu.add_command(label='发送到设备…', command=self.send_to_device)
        self.menu.add_separator()
        self.menu.add_command(label='挂载对方磁盘…', command=self.mount_dialog)
        self.menu.add_separator()
        self.menu.add_command(label='重命名', command=self.rename_selected)
        self.menu.add_command(label='删除', command=self.delete_selected)

    def _on_tree_select(self, event=None):
        sel = self.tree.selection()
        self._selected_iid = sel[0] if sel else None

    # ---------------- 设备 ----------------
    def refresh_devices(self):
        def work():
            try:
                devs = self.state.devices_list()
            except Exception:
                devs = []
            self.queue.put(('devices', devs))
        threading.Thread(target=work, daemon=True).start()

    def _render_devices(self, devs):
        self.devices = devs
        sel = self.dev_list.curselection()
        keep = self.dev_list.get(sel[0]) if sel else None
        self.dev_list.delete(0, 'end')
        self.dev_list.insert('end', '💻  本机（这台电脑）')
        for d in devs:
            mark = '🟣' if d.get('online') else '⚪'
            scope = '全盘' if d.get('whole_disk') else (d.get('label') or '共享目录')
            self.dev_list.insert('end', '%s %s  ·  %s' % (mark, d.get('name'), scope))
        # 尽量保持原选择
        if keep:
            try:
                idx = list(self.dev_list.get(0, 'end')).index(keep)
                self.dev_list.selection_clear(0, 'end')
                self.dev_list.selection_set(idx)
            except ValueError:
                pass
        scope = '全部磁盘' if self.cfg.allow_whole_disk else os.path.basename(
            self.cfg.root.rstrip('\\/')) or self.cfg.root
        self.lbl_self.config(text='%s · %s · %s'
                             % (self.cfg.name, scope,
                                '、'.join(local_ips()[:1]) or '未联网'))

    def _selected_device(self):
        """返回 (dev_id, 显示名)；本机返回 ('', '本机')。"""
        sel = self.dev_list.curselection()
        if not sel:
            return '', '本机'
        idx = sel[0]
        if idx == 0:
            return '', '本机'
        devs = self.devices
        if 0 < idx <= len(devs):
            d = devs[idx - 1]
            return d.get('id'), d.get('name')
        return '', '本机'

    def _on_pick_device(self):
        dev_id, name = self._selected_device()
        self.cur = {'dev': dev_id, 'path': ''}
        self.navigate('')

    # ---------------- 挂载远端磁盘 ----------------
    def mount_dialog(self):
        c = THEME
        dlg = tk.Toplevel(self.root)
        dlg.title('挂载局域网磁盘')
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=c['bg'])
        frm = ttk.Frame(dlg, padding=22)
        frm.pack(fill='both', expand=True)

        ttk.Label(frm, text='把别的电脑的磁盘挂到「我的电脑」里 🗂️',
                  font=(FONT, 13, 'bold')).pack(anchor='w')
        ttk.Label(frm, text='挂好之后会多出一个网络驱动器，双击就能直接浏览、复制，'
                            '和在本地一样。',
                  style='Muted.TLabel', wraplength=430,
                  justify='left').pack(anchor='w', pady=(6, 16))

        online = [d for d in self.devices if d.get('online')]
        if not online:
            ttk.Label(frm, text='现在还没有在线的设备，先让对方也打开 LanShare。',
                      foreground=c['danger'], wraplength=380,
                      justify='left').pack(anchor='w')
            RoundButton(frm, text='知道啦', command=dlg.destroy, kind='accent',
                        bg=c['bg'], font=(FONT, 11)).pack(anchor='e', pady=(14, 0))
            return

        names = []
        for d in online:
            scope = '全盘' if d.get('whole_disk') else (d.get('label') or '共享目录')
            names.append('%s（%s · %s）' % (d.get('name'), scope, d.get('host')))
        dev_var = tk.StringVar(value=names[0])
        ttk.Label(frm, text='挂载哪台电脑：', style='Card.TLabel').pack(anchor='w')
        combo = ttk.Combobox(frm, values=names, textvariable=dev_var,
                             state='readonly', width=48, font=(FONT, 11))
        combo.pack(fill='x', pady=(6, 14))

        # 挂载对方哪个盘。注意：不能挂「此电脑」这个虚拟根，
        # Windows 的 WebDAV 客户端浏览它会报"找不到网络路径"，必须选具体盘。
        ttk.Label(frm, text='挂载对方的哪个盘：', style='Card.TLabel').pack(anchor='w')
        sub_row = ttk.Frame(frm)
        sub_row.pack(fill='x', pady=(6, 14))
        sub_var = tk.StringVar(value='')
        sub_vals = ['（正在读取…）']
        sub_box = ttk.Combobox(sub_row, values=sub_vals, textvariable=sub_var,
                               state='readonly', width=18, font=(FONT, 11))
        sub_box.pack(side='left')
        hint2 = ttk.Label(sub_row, text='', style='CardMuted.TLabel')
        hint2.pack(side='left', padx=8)

        def load_drives(*_):
            idx = names.index(dev_var.get())
            dev = online[idx]
            whole = bool(dev.get('whole_disk'))
            if not whole:
                sub_var.set('共享目录')
                sub_box.config(values=['共享目录'])
                hint2.config(text='对方只共享了一个目录')
                return
            sub_var.set('')
            sub_box.config(values=['（正在读取…）'])
            hint2.config(text='正在读取对方的磁盘列表…')

            def work():
                vals = []
                try:
                    url = 'http://%s:%d/api/info' % (
                        dev.get('host'), int(dev.get('port') or 8765))
                    import json as _json
                    import urllib.request
                    with urllib.request.urlopen(url, timeout=6) as resp:
                        info = _json.loads(resp.read().decode('utf-8'))
                    vals = list(info.get('drives') or [])
                except Exception:
                    vals = []
                self.queue.put(('mount_drives', sub_box, sub_var, hint2, vals))

            threading.Thread(target=work, daemon=True).start()

        dev_var.trace_add('write', load_drives)

        free = lanshare.list_free_drives()
        ttk.Label(frm, text='用哪个盘符：', style='Card.TLabel').pack(anchor='w')
        drive_var = tk.StringVar(value=free[0] if free else '')
        ttk.Combobox(frm, values=free, textvariable=drive_var, state='readonly',
                     width=12, font=(FONT, 11)).pack(anchor='w', pady=(6, 14))

        mounted = lanshare.mounted_lanshare_drives()
        if mounted:
            ttk.Label(frm, text='已挂载：%s' % '、'.join(mounted),
                      style='CardMuted.TLabel').pack(anchor='w', pady=(0, 6))
            row = ttk.Frame(frm)
            row.pack(fill='x', pady=(0, 8))
            mvar = tk.StringVar(value=mounted[0])
            ttk.Combobox(row, values=mounted, textvariable=mvar, state='readonly',
                         width=12, font=(FONT, 11)).pack(side='left')

            def do_unmount():
                ok, msg = lanshare.unmount_webdav(mvar.get())
                if ok:
                    bubble_toast(self.root, msg)
                    dlg.destroy()
                    self.mount_dialog()
                else:
                    bubble_error(self.root, msg)

            RoundButton(row, text='断开这个盘', command=do_unmount,
                        font=(FONT, 11)).pack(side='left', padx=8)

        info = ttk.Label(frm, text='', style='CardMuted.TLabel',
                         wraplength=430, justify='left')
        info.pack(anchor='w', pady=(2, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=(12, 0))
        RoundButton(btns, text='取消', command=dlg.destroy,
                    font=(FONT, 11)).pack(side='right')
        start = RoundButton(btns, text='挂载', command=lambda: do_mount(),
                            kind='accent', font=(FONT, 11))
        start.pack(side='right', padx=8)

        def do_mount():
            idx = names.index(dev_var.get())
            dev = online[idx]
            drive = drive_var.get()
            if not drive:
                info.config(text='没有可用的盘符了，先断开一个再挂。',
                            foreground=THEME['danger'])
                return
            start.set_enabled(False)
            info.config(text='正在挂载… 如果弹出"用户账户控制"，点「是」就行。',
                        foreground=THEME['muted'])

            def work():
                base = '%s://%s:%d%s' % (
                    'https' if dev.get('https') else 'http',
                    dev.get('host'), int(dev.get('port') or 8765),
                    lanshare.DAV_BASE)
                # 全盘模式必须挂具体某个盘（虚拟根挂上打不开）
                chosen = sub_var.get().strip()
                if chosen and chosen not in ('共享目录',) \
                        and not chosen.startswith('（'):
                    sub = chosen.rstrip(':')
                    url = '%s/%s/' % (base, sub)
                else:
                    url = base + '/'
                try:
                    ok, msg = lanshare.mount_webdav(
                        url, drive, password=self.cfg.key)
                    self.queue.put(('mount_done', dlg, ok, msg, drive if ok else ''))
                except Exception as exc:
                    self.queue.put(('mount_done', dlg, False, str(exc), ''))

            threading.Thread(target=work, daemon=True).start()

    def add_device_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title('添加设备')
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg = tk.Toplevel(self.root)
        dlg.title('添加设备')
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text='对方电脑的 IP（可带端口，如 192.168.3.6 或 192.168.3.6:8765）').pack(anchor='w')
        var = tk.StringVar()
        ent = ttk.Entry(frm, textvariable=var, width=36, font=(MONO, 10))
        ent.pack(fill='x', pady=(6, 4))
        ttk.Label(frm, text='如果对方设了访问密码，请填写（没有就留空）', foreground='#666').pack(anchor='w')
        kvar = tk.StringVar()
        ttk.Entry(frm, textvariable=kvar, width=36, show='•').pack(fill='x', pady=(4, 10))
        info = ttk.Label(frm, text='', foreground='#a00')
        info.pack(anchor='w')

        def do_add():
            host = var.get().strip()
            if not host:
                info.config(text='请输入 IP 地址')
                return
            # Tk 变量只能在主线程读，先取出来再进后台线程
            given_key = kvar.get().strip() or self.cfg.key
            btn.config(state='disabled')
            info.config(text='正在连接…', foreground='#666')

            def work():
                try:
                    port = None
                    h = host
                    if ':' in host:
                        h, _, p = host.partition(':')
                        port = p
                    probe = {'id': 'probe', 'name': h, 'host': h,
                             'port': int(port) if port else 8765,
                             'https': False}
                    rd = RemoteDirectory(probe, key=given_key, timeout=8)
                    res = rd.ping()
                    pong = res.get('info') or {}
                    self.state.upsert_device({
                        'id': pong.get('id'), 'name': pong.get('name') or h,
                        'port': probe['port'], 'version': pong.get('version'),
                        'platform': pong.get('platform'), 'label': pong.get('root') or '',
                        'whole_disk': pong.get('whole_disk', False),
                    }, ip=h, port=probe['port'], source='manual')
                    self.queue.put(('added', pong.get('name') or h, res.get('rtt')))
                    self.queue.put(('close_dialog', dlg))
                except RemoteError as exc:
                    msg = ('对方需要正确的访问密码' if exc.status == 401
                           else '连接失败：%s' % exc.message)
                    self.queue.put(('dialog_error', info, msg))
                    self.queue.put(('enable_btn', btn))
                except Exception as exc:
                    self.queue.put(('dialog_error', info, '连接失败：%s' % exc))
                    self.queue.put(('enable_btn', btn))

            threading.Thread(target=work, daemon=True).start()

        btn = RoundButton(frm, text='添加', command=do_add, kind='accent',
                          bg=c['surface'], font=(FONT, 11))
        btn.pack(anchor='e', pady=(8, 0))
        ent.focus_set()
        dlg.bind('<Return>', lambda e: do_add())

    # ---------------- 文件浏览 ----------------
    def _provider(self, dev_id):
        if not dev_id:
            return LocalDirectory(self.cfg.root, whole_disk=self.cfg.allow_whole_disk)
        dev = self.state.get_device(dev_id)
        if not dev:
            raise RuntimeError('设备不在线，请刷新设备列表')
        return RemoteDirectory(dev, key=self.cfg.key, timeout=60)

    def navigate(self, path):
        dev_id = self.cur['dev']
        self.cur['path'] = norm_rel(path)
        # 注意：Tk 变量只能在主线程读，所以先在这里取好，再传给后台线程
        show_hidden = bool(self.var_hidden.get())
        self.lbl_status.config(text='正在加载…')
        target_path = self.cur['path']

        def work():
            try:
                p = self._provider(dev_id)
                entries = p.list_dir(target_path, include_hidden=show_hidden)
                self.queue.put(('entries', dev_id, target_path, entries, None))
            except Exception as exc:
                self.queue.put(('entries', dev_id, target_path, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def go_up(self):
        p = self.cur['path']
        if not p:
            return
        self.navigate('/'.join(p.split('/')[:-1]))

    def _render_entries(self, dev_id, path, entries, error):
        if dev_id != self.cur['dev'] or path != self.cur['path']:
            return                      # 位置已变，丢弃过期结果
        self.tree.delete(*self.tree.get_children())
        if error:
            self.lbl_status.config(text='加载失败：%s' % error)
            self.lbl_path.config(text='⚠ %s' % error)
            return
        self.entries = entries or []
        if not self.entries:
            self.tree.insert('', 'end', iid='__empty__',
                             values=('📭  这个文件夹是空的', '', '', ''),
                             tags=('even',))
            self.lbl_path.config(text='空文件夹')
            self.lbl_status.config(text='就绪')
            return
        for i, e in enumerate(self.entries):
            if e.get('drive'):
                size_txt = ('%s 可用 / %s' % (human_size(e.get('free')), human_size(e.get('total')))
                            if e.get('total') else '磁盘')
            elif e.get('is_dir'):
                cnt = e.get('count')
                size_txt = ('%d 项' % cnt) if cnt is not None else '—'
            else:
                size_txt = human_size(e.get('size'))
            name = e.get('name') or ''
            if e.get('drive') and e.get('label'):
                name += '（%s）' % e['label']
            name_txt = '%s  %s' % (icon_for(e), name)
            tags = ['even' if i % 2 == 0 else 'odd']
            if e.get('is_dir') or e.get('drive'):
                tags.append('dir')
            self.tree.insert('', 'end', iid=str(i),
                             values=(name_txt, type_text(e), size_txt,
                                     fmt_time(e.get('mtime'))),
                             tags=tuple(tags))
        dev_id_cur, dev_name = self._selected_device()
        where = '本机' if not dev_id_cur else dev_name
        shown = '/ ' + path if path else ''
        n_dir = sum(1 for e in self.entries if e.get('is_dir') or e.get('drive'))
        n_file = len(self.entries) - n_dir
        self.lbl_path.config(text='%s%s    共 %d 项 · %d 个文件夹 · %d 个文件'
                             % (where, shown, len(self.entries), n_dir, n_file))
        self.lbl_status.config(text='就绪')

    def _selected_entries(self):
        out = []
        for iid in self.tree.selection():
            try:
                out.append(self.entries[int(iid)])
            except (ValueError, IndexError):
                pass
        return out

    def _rel_of(self, entry):
        return join_rel(self.cur['path'], entry['name'])

    def _on_double(self, event=None):
        sel = self._selected_entries()
        if len(sel) == 1 and sel[0].get('is_dir'):
            self.navigate(self._rel_of(sel[0]))
        elif sel:
            self.download_selected()

    def open_selected(self):
        sel = self._selected_entries()
        if len(sel) == 1 and sel[0].get('is_dir'):
            self.navigate(self._rel_of(sel[0]))
        elif sel:
            self.download_selected()

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    # ---------------- 下载 ----------------
    def remember_download_dir(self, target):
        """记住用户选的下载目录，下次默认用它。"""
        if not target:
            return
        self.last_download_dir = target
        self.cfg.download_dir = target
        self.cfg.save()

    def download_selected(self):
        sel = self._selected_entries()
        if not sel:
            bubble_info(self.root, '请先选中要下载的文件或文件夹哦 🐾')
            return
        dev_id, dev_name = self._selected_device()
        target = filedialog.askdirectory(
            parent=self.root, title='选择保存到哪个文件夹',
            initialdir=self.last_download_dir or os.path.expanduser('~'))
        if not target:
            return
        self.remember_download_dir(target)

        items = [(self._rel_of(e), e) for e in sel]
        total_files = sum(1 for _, e in items if not e.get('is_dir'))
        task = GuiTask('download', '下载 %d 项到 %s' % (len(items), target),
                       total_files=total_files)
        self.tasks.append(task)
        self._show_task(task)
        threading.Thread(target=self._run_download, daemon=True,
                         args=(task, dev_id, dev_name, items, target)).start()

    def _run_download(self, task, dev_id, dev_name, items, target):
        try:
            src = self._provider(dev_id)
            # 下载目标是本机用户自己选的位置，不受「共享目录」限制
            dst = LocalDirectory('', whole_disk=True)
            dest_rel = abs_to_rel(target)
            all_errors = []
            for rel, entry in items:
                if task.cancel:
                    break
                payload = build_payload(src, rel)
                task.total_bytes += sum(int(f.get('size') or 0) for f in payload['files'])
                # 直接把任务本身传进去：取消/暂停/进度都能实时生效
                ok, skipped, errors, moved = run_transfer(
                    src, dst, payload, task, dest_base=dest_rel)
                all_errors += errors
            task.errors = all_errors
            task.status = 'canceled' if task.cancel else ('error' if all_errors else 'done')
            task.message = '已保存到 %s' % target
        except Exception as exc:
            task.status = 'error'
            task.errors.append(str(exc))
        finally:
            task.finished = time.time()
            self.queue.put(('task', task))

    # ---------------- 上传（往当前目录放东西）----------------
    def upload_files(self):
        if self.cur['dev']:
            self.send_to_device()      # 浏览远端时，用「发送」代替上传
            return
        if not self.cur['path'] and self.cfg.allow_whole_disk:
            bubble_info(self.root, '先进入某个磁盘或文件夹，再上传吧 ✨')
            return
        files = filedialog.askopenfilenames(parent=self.root, title='选择要上传的文件')
        if not files:
            return
        self._copy_into(files, join_rel(self.cur['path'], ''))

    def upload_folder(self):
        if self.cur['dev']:
            bubble_info(self.root, '浏览别的电脑时，请用「发送到设备」哦 🌈')
            return
        if not self.cur['path'] and self.cfg.allow_whole_disk:
            bubble_info(self.root, '先进入某个磁盘或文件夹，再上传吧 ✨')
            return
        folder = filedialog.askdirectory(parent=self.root, title='选择要上传的文件夹')
        if folder:
            self._copy_into([folder], join_rel(self.cur['path'], ''))

    def _copy_into(self, paths, dest_rel):
        items = []
        for p in paths:
            p = os.path.abspath(p)
            name = os.path.basename(p)
            items.append((p, join_rel(dest_rel, name), os.path.isdir(p)))
        task = GuiTask('copy', '复制 %d 项到 %s' % (len(items), dest_rel or '/'),
                       total_files=len(items))
        self.tasks.append(task)
        self._show_task(task)
        threading.Thread(target=self._run_local_copy, daemon=True,
                         args=(task, items, dest_rel)).start()

    def _run_local_copy(self, task, items, dest_rel):
        dst = LocalDirectory('', whole_disk=True)
        try:
            for src_path, target_rel, is_dir in items:
                if task.cancel:
                    break
                task.current = os.path.basename(src_path)
                src = LocalDirectory(os.path.dirname(src_path) or src_path, whole_disk=True)
                base = norm_rel(src_path)
                payload = build_payload(src, base)
                task.total_bytes += sum(int(f.get('size') or 0) for f in payload['files'])
                ok, skipped, errors, moved = run_transfer(src, dst, payload, task,
                                                          dest_base=dest_rel)
                task.errors += errors
            task.status = 'canceled' if task.cancel else ('error' if task.errors else 'done')
            task.message = '已复制到 %s' % (dest_rel or '/')
        except Exception as exc:
            task.status = 'error'
            task.errors.append(str(exc))
        finally:
            task.finished = time.time()
            self.queue.put(('task', task))

    # ---------------- 发送到其它电脑 ----------------
    def send_to_device(self):
        sel = self._selected_entries()
        if not sel:
            bubble_info(self.root, '请先选中要发送的内容呀 🎀')
            return
        if not self.cur['dev'] and not self.cur['path'] and self.cfg.allow_whole_disk:
            bubble_info(self.root, '先进入某个磁盘或文件夹吧 🗂️')
            return
        dev_id, _ = self._selected_device()
        targets = [d for d in self.devices if d.get('online') and d.get('id') != dev_id]
        if not targets:
            bubble_info(self.root, '还没有其它在线设备呢 🫧\n'
                                   '确认对方也打开了 LanShare，'
                                   '或在左边点「添加设备 IP」把对方加进来。')
            return
        dlg = tk.Toplevel(self.root)
        dlg.title('发送到设备')
        dlg.transient(self.root)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text='要发送 %d 项，选择接收的电脑（可多选）：' % len(sel),
                  font=(FONT, 10, 'bold')).pack(anchor='w')
        vars_ = []
        for d in targets:
            v = tk.BooleanVar(value=len(targets) == 1)
            vars_.append((d, v))
            ttk.Checkbutton(frm, text='%s  ·  %s:%s%s' % (
                d.get('name'), d.get('host'), d.get('port'),
                '  （全部磁盘）' if d.get('whole_disk') else ''),
                variable=v).pack(anchor='w', pady=1)
        ttk.Label(frm, text='保存到对方哪个目录（留空 = 对方的默认接收目录）').pack(anchor='w', pady=(10, 2))
        dest_var = tk.StringVar()
        ttk.Entry(frm, textvariable=dest_var, width=42).pack(fill='x')
        info = ttk.Label(frm, text='', foreground='#a00')
        info.pack(anchor='w', pady=(4, 0))

        def do_send():
            chosen = [d for d, v in vars_ if v.get()]
            if not chosen:
                info.config(text='请至少选一台设备')
                return
            dlg.destroy()
            self._start_send(sel, chosen, dest_var.get().strip())

        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=(12, 0))
        RoundButton(btns, text='取消', command=dlg.destroy,
                    font=(FONT, 11)).pack(side='right')
        RoundButton(btns, text='开始发送', command=do_send, kind='accent',
                    font=(FONT, 11)).pack(side='right', padx=8)

    def _start_send(self, entries, targets, dest_base):
        for d in targets:
            items = [(self._rel_of(e), e) for e in entries]
            task = GuiTask('send', '发送到 %s' % d.get('name'),
                           total_files=sum(1 for _, e in items if not e.get('is_dir')))
            self.tasks.append(task)
            self._show_task(task)
            threading.Thread(target=self._run_send, daemon=True,
                             args=(task, d, items, dest_base)).start()

    def _run_send(self, task, target_dev, items, dest_base):
        try:
            src = self._provider(self.cur['dev'])
            dst = RemoteDirectory(target_dev, key=self.cfg.key, timeout=120)
            cfg = self.cfg
            sender = {'id': self.state.self_id, 'name': cfg.name,
                      'host': (local_ips() or ['127.0.0.1'])[0],
                      'port': cfg.port, 'https': cfg.https}
            all_errors = []
            for rel, entry in items:
                if task.cancel:
                    break
                payload = build_payload(src, rel)
                # 先通知对方登记接收
                try:
                    manifest = list(payload['files']) + [
                        {'rel': d['rel'], 'is_dir': True, 'size': 0} for d in payload['dirs']]
                    resp = dst.announce_incoming(sender, manifest, dest_base, 'skip')
                    if resp.get('path'):
                        dest_base = norm_rel(resp['path'])
                except RemoteError as exc:
                    if exc.status == 403:
                        raise RuntimeError('对方没有开启「允许其它设备写入」，'
                                           '请在他的窗口里开启后重试')
                    if exc.status not in (404,):
                        raise
                task.total_bytes += sum(int(f.get('size') or 0) for f in payload['files'])
                ok, skipped, errors, moved = run_transfer(
                    src, dst, payload, task, dest_base=dest_base, sender=sender)
                all_errors += errors
            task.errors = all_errors
            task.status = 'canceled' if task.cancel else ('error' if all_errors else 'done')
            task.message = '已发送到 %s' % target_dev.get('name')
        except Exception as exc:
            task.status = 'error'
            task.errors.append(str(exc))
        finally:
            task.finished = time.time()
            self.queue.put(('task', task))

    # ---------------- 文件操作 ----------------
    def new_folder(self):
        if self.cur['dev']:
            bubble_info(self.root, '这个要在对方电脑上新建文件夹哦 🐾')
            return
        if not self.cur['path'] and self.cfg.allow_whole_disk:
            bubble_info(self.root, '先进入某个磁盘或文件夹吧 🗂️')
            return
        dlg = bubble_prompt(self.root, '新建文件夹', '给新文件夹起个名字吧 ✨')
        if not dlg:
            return
        try:
            LocalDirectory(self.cfg.root, whole_disk=self.cfg.allow_whole_disk).mkdir(
                join_rel(self.cur['path'], dlg))
            self.navigate(self.cur['path'])
            bubble_toast(self.root, '新文件夹建好啦')
        except Exception as exc:
            bubble_error(self.root, '没能新建成功 💧\n%s' % exc)

    def rename_selected(self):
        sel = self._selected_entries()
        if len(sel) != 1:
            bubble_info(self.root, '一次只能重命名一项哦 ✏️')
            return
        if self.cur['dev']:
            bubble_info(self.root, '这个要在对方电脑上重命名哦 🐾')
            return
        e = sel[0]
        new = bubble_prompt(self.root, '重命名', '换个新名字吧 ✏️', e['name'])
        if not new or new == e['name']:
            return
        try:
            LocalDirectory(self.cfg.root, whole_disk=self.cfg.allow_whole_disk).rename(
                self._rel_of(e), new)
            self.navigate(self.cur['path'])
            bubble_toast(self.root, '名字改好啦')
        except Exception as exc:
            bubble_error(self.root, '改名没成功 💧\n%s' % exc)

    def delete_selected(self):
        sel = self._selected_entries()
        if not sel:
            return
        if self.cur['dev']:
            bubble_info(self.root, '这个要到对方电脑上删除哦 🐾')
            return
        names = '、'.join(e['name'] for e in sel[:5])
        if len(sel) > 5:
            names += ' 等 %d 项' % len(sel)
        if not bubble_ask(self.root, '确定要删除 %s 吗？\n删掉就找不回来啦～' % names,
                          title='真的删掉吗', yes='删掉吧', no='再想想'):
            return
        d = LocalDirectory(self.cfg.root, whole_disk=self.cfg.allow_whole_disk)
        fails = []
        for e in sel:
            try:
                d.remove(self._rel_of(e))
            except Exception as exc:
                fails.append('%s: %s' % (e['name'], exc))
        if fails:
            bubble_error(self.root, '有几项没能删掉 💧\n%s' % '\n'.join(fails[:6]))
        else:
            bubble_toast(self.root, '已经删掉啦')
        self.navigate(self.cur['path'])

    # ---------------- 传输面板 ----------------
    def _show_task(self, task):
        self.lbl_pct.config(text=task.title[:40])
        self.prog['value'] = 0

    def open_transfers(self):
        dlg = tk.Toplevel(self.root)
        dlg.title('传输记录')
        dlg.geometry('760x420')
        tree = ttk.Treeview(dlg, columns=('prog', 'size', 'status'), show='tree headings')
        tree.heading('#0', text='任务')
        tree.heading('prog', text='进度')
        tree.heading('size', text='已传 / 总量')
        tree.heading('status', text='状态')
        tree.column('#0', width=320)
        tree.column('prog', width=90, anchor='e')
        tree.column('size', width=180, anchor='e')
        tree.column('status', width=140)
        tree.pack(fill='both', expand=True, padx=10, pady=10)
        st = {'done': '完成', 'running': '进行中', 'error': '出错',
              'canceled': '已取消', 'paused': '已暂停'}

        def refresh():
            if not dlg.winfo_exists():
                return
            tree.delete(*tree.get_children())
            for t in reversed(self.tasks[-100:]):
                tree.insert('', 'end', text=t.title,
                            values=('%.1f%%' % t.calc_progress(),
                                    '%s / %s' % (human_size(t.sent_bytes), human_size(t.total_bytes)),
                                    st.get(t.status, t.status)))
            dlg.after(1000, refresh)
        refresh()
        ttk.Button(dlg, text='关闭', command=dlg.destroy).pack(pady=(0, 10))

    # ---------------- 设置 ----------------
    def open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title('设置')
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill='both', expand=True)

        ttk.Label(frm, text='常规', font=(FONT, 11, 'bold')).grid(row=0, column=0, sticky='w', pady=(0, 6))
        ttk.Label(frm, text='本机名称').grid(row=1, column=0, sticky='w')
        name_var = tk.StringVar(value=self.cfg.name)
        ttk.Entry(frm, textvariable=name_var, width=34).grid(row=1, column=1, sticky='w', padx=8, pady=3)

        ttk.Label(frm, text='默认下载目录').grid(row=2, column=0, sticky='w')
        dl_var = tk.StringVar(value=self.cfg.download_dir or '')
        dl_row = ttk.Frame(frm)
        dl_row.grid(row=2, column=1, sticky='we', padx=8, pady=3)
        ttk.Entry(dl_row, textvariable=dl_var, width=26).pack(side='left', fill='x', expand=True)
        ttk.Button(dl_row, text='浏览…', command=lambda: (
            dl_var.set(filedialog.askdirectory(parent=dlg, title='选择默认下载目录') or dl_var.get()))
        ).pack(side='left', padx=4)

        ttk.Label(frm, text='文件可访问范围').grid(row=3, column=0, sticky='w')
        scope = ttk.Frame(frm)
        scope.grid(row=3, column=1, sticky='w', padx=8, pady=3)
        scope_var = tk.BooleanVar(value=bool(self.cfg.allow_whole_disk))
        ttk.Radiobutton(scope, text='本机全部磁盘（推荐）', value=True,
                        variable=scope_var).pack(anchor='w')
        ttk.Radiobutton(scope, text='只允许访问指定目录：', value=False,
                        variable=scope_var).pack(anchor='w')
        root_var = tk.StringVar(value=self.cfg.root or '')
        root_row = ttk.Frame(scope)
        root_row.pack(fill='x')
        ttk.Entry(root_row, textvariable=root_var, width=26).pack(side='left', fill='x', expand=True)
        ttk.Button(root_row, text='浏览…', command=lambda: (
            root_var.set(filedialog.askdirectory(parent=dlg, title='选择共享目录') or root_var.get()))
        ).pack(side='left', padx=4)

        ttk.Separator(frm, orient='horizontal').grid(row=4, column=0, columnspan=2,
                                                     sticky='we', pady=12)
        ttk.Label(frm, text='后台与启动', font=(FONT, 11, 'bold')).grid(row=5, column=0, sticky='w', pady=(0, 6))
        auto_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(frm, text='开机自动启动（静默在右下角托盘运行）',
                        variable=auto_var).grid(row=6, column=0, columnspan=2, sticky='w', pady=2)
        accept_var = tk.BooleanVar(value=bool(self.cfg.auto_accept))
        ttk.Checkbutton(frm, text='允许其它电脑直接写入本机（关闭则每次都要确认）',
                        variable=accept_var).grid(row=7, column=0, columnspan=2, sticky='w', pady=2)

        ttk.Separator(frm, orient='horizontal').grid(row=8, column=0, columnspan=2,
                                                     sticky='we', pady=12)
        ttk.Label(frm, text='访问地址（把这些地址告诉其它电脑或手机）',
                  font=(FONT, 11, 'bold')).grid(row=9, column=0, columnspan=2, sticky='w')
        url_box = tk.Listbox(frm, height=3, font=(MONO, 9), activestyle='none')
        for ip in local_ips():
            url_box.insert('end', 'http://%s:%s/' % (ip, self.cfg.port))
        url_box.grid(row=10, column=0, columnspan=2, sticky='we', pady=6)

        msg = ttk.Label(frm, text='', foreground='#080')
        msg.grid(row=11, column=0, columnspan=2, sticky='w')

        def save():
            self.cfg.name = name_var.get().strip() or self.cfg.name
            self.cfg.download_dir = dl_var.get().strip()
            self.cfg.allow_whole_disk = bool(scope_var.get())
            if not self.cfg.allow_whole_disk:
                r = root_var.get().strip()
                if not r or not os.path.isdir(r):
                    msg.config(text='共享目录无效', foreground='#a00')
                    return
                self.cfg.root = os.path.abspath(r)
            self.cfg.auto_accept = bool(accept_var.get())
            self.cfg.save()
            ok, text = set_autostart(bool(auto_var.get()))
            msg.config(text=text, foreground='#080' if ok else '#a00')
            self.lbl_self.config(text='本机 %s · %s' % (
                self.cfg.name,
                '全部磁盘' if self.cfg.allow_whole_disk else self.cfg.root))
            self.navigate('')

        btns = ttk.Frame(frm)
        btns.grid(row=12, column=0, columnspan=2, sticky='e', pady=(14, 0))
        RoundButton(btns, text='关闭', command=dlg.destroy,
                    font=(FONT, 11)).pack(side='right')
        RoundButton(btns, text='保存', command=save, kind='accent',
                    font=(FONT, 11)).pack(side='right', padx=8)

    # ---------------- 主循环辅助 ----------------
    def _pump(self):
        """在主线程里处理后台线程发来的消息。"""
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == 'devices':
                    self._render_devices(msg[1])
                elif kind == 'entries':
                    self._render_entries(msg[1], msg[2], msg[3], msg[4])
                elif kind == 'task':
                    self._update_task_ui(msg[1])
                elif kind == 'added':
                    self.lbl_status.config(text='已添加设备 %s（延迟 %s ms）' % (msg[1], msg[2]))
                    self.refresh_devices()
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
                elif kind == 'mount_done':
                    _, dlg, ok, text, drive = msg
                    try:
                        dlg.destroy()
                    except Exception:
                        pass
                    if ok:
                        bubble_ok(self.root,
                                  '已经挂到「我的电脑」里啦 🎉\n'
                                  '打开资源管理器就能看到 %s，直接双击就能用。'
                                  % drive)
                    else:
                        bubble_error(self.root, '没能挂上 💧\n%s' % text)
                elif kind == 'mount_drives':
                    _, box, var, hint, vals = msg
                    try:
                        if vals:
                            box.config(values=vals)
                            var.set(vals[0])
                            hint.config(text='共 %d 个磁盘' % len(vals))
                        else:
                            box.config(values=['（没读到磁盘列表）'])
                            hint.config(text='没读到对方的磁盘，稍后可重试')
                    except Exception:
                        pass
                elif kind == 'quit':
                    self._really_quit()
                    return
                elif kind == 'open':
                    self.show()
                elif kind == 'open_browser':
                    self.open_in_browser()
                elif kind == 'copy_url':
                    self.copy_access_url()
        except queue.Empty:
            pass
        if self.visible:
            self._refresh_running_task()
        # 定期自动刷新设备列表，让别的电脑上线/下线能及时反映出来
        self._pump_ticks = getattr(self, '_pump_ticks', 0) + 1
        if self._pump_ticks % 25 == 0:      # 约每 10 秒
            self.refresh_devices()
        try:
            self.root.after(400, self._pump)
        except Exception:
            pass

    def _update_task_ui(self, task):
        if task.status == 'done':
            self.lbl_status.config(text='%s —— 完成' % task.title)
            self.prog['value'] = 100
            self.lbl_pct.config(text='100%')
            self.navigate(self.cur['path'])
        elif task.status == 'error':
            self.lbl_status.config(text='%s —— 失败' % task.title)
            errs = '\n'.join(task.errors[:5])
            bubble_error(self.root, '传输失败啦 💧\n%s\n\n%s' % (task.title, errs))

    def _refresh_running_task(self):
        running = [t for t in self.tasks if t.status == 'running']
        if not running:
            return
        t = running[-1]
        prog = t.calc_progress()
        self.prog['value'] = prog
        self.lbl_pct.config(text='%.1f%%  %s/s' % (prog, human_size(t.speed_now)))
        self.lbl_status.config(text='%s：%s' % (t.title, t.current or '准备中…'))

    # ---------------- 显示 / 隐藏 / 退出 ----------------
    def show(self):
        self.visible = True
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        self.refresh_devices()

    def hide(self):
        """关闭窗口 = 收进托盘，服务继续跑。"""
        self.visible = False
        try:
            self.root.withdraw()
        except Exception:
            pass
        self._toast('LanShare 仍在后台运行，双击右下角图标可以重新打开。')

    def _toast(self, text):
        try:
            self._tray_notify and self._tray_notify(text)
        except Exception:
            pass

    def access_urls(self):
        """本机可被访问的地址（托盘和设置页共用）。"""
        scheme = 'https' if self.cfg.https else 'http'
        q = ('?key=%s' % urllib.parse.quote(self.cfg.key)) if self.cfg.key else ''
        return ['%s://%s:%d/%s' % (scheme, ip, self.cfg.port, q) for ip in local_ips()]

    def open_in_browser(self):
        urls = self.access_urls()
        if urls:
            try:
                webbrowser.open(urls[0])
            except Exception:
                pass

    def copy_access_url(self):
        urls = self.access_urls()
        if not urls:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(urls[0])
            self.lbl_status.config(text='访问地址已复制：%s' % urls[0])
        except Exception:
            pass

    def _really_quit(self):
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        self.on_quit()

    def run(self):
        self.root.mainloop()


def _prompt(parent, title, label, initial=''):
    """简单的单行输入对话框。"""
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.resizable(False, False)
    frm = ttk.Frame(dlg, padding=14)
    frm.pack(fill='both', expand=True)
    ttk.Label(frm, text=label).pack(anchor='w')
    var = tk.StringVar(value=initial)
    ent = ttk.Entry(frm, textvariable=var, width=36, font=(FONT, 10))
    ent.pack(fill='x', pady=(6, 10))
    result = {'v': None}

    def ok():
        result['v'] = var.get().strip()
        dlg.destroy()
    btns = ttk.Frame(frm)
    btns.pack(fill='x')
    RoundButton(btns, text='取消', command=dlg.destroy,
                font=(FONT, 11)).pack(side='right')
    RoundButton(btns, text='确定', command=ok, kind='accent',
                font=(FONT, 11)).pack(side='right', padx=8)
    ent.focus_set()
    ent.select_range(0, 'end')
    dlg.bind('<Return>', lambda e: ok())
    dlg.grab_set()
    parent.wait_window(dlg)
    return result['v']


# ---------------------------------------------------------------------------
# 系统托盘
# ---------------------------------------------------------------------------

class Tray(object):
    """右下角托盘图标。pystray 在自己的线程里跑，回调通过 UI 队列转交主线程。"""

    def __init__(self, window, state, cfg):
        self.window = window
        self.state = state
        self.cfg = cfg
        self.icon = None
        self.thread = None

    def _make_image(self):
        from PIL import Image
        try:
            from lanshare_icon import render_rgba
            size = 64
            return Image.frombytes('RGBA', (size, size), bytes(render_rgba(size)))
        except Exception:
            # 兜底：画一个纯色圆
            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            px = img.load()
            for y in range(64):
                for x in range(64):
                    if (x - 32) ** 2 + (y - 32) ** 2 <= 28 * 28:
                        px[x, y] = (47, 109, 246, 255)
            return img

    def start(self):
        try:
            import pystray
        except ImportError:
            log.warning('未安装 pystray，无法显示托盘图标（窗口关闭时会直接退出）')
            return False
        menu = pystray.Menu(
            pystray.MenuItem('打开主界面', self._on_open, default=True),
            pystray.MenuItem('打开网页界面', self._on_browser),
            pystray.MenuItem('复制访问地址', self._on_copy),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出 LanShare', self._on_quit),
        )
        self.icon = pystray.Icon('LanShare', self._make_image(),
                                 'LanShare —— 局域网文件互传', menu)
        self.thread = threading.Thread(target=self._run, name='ls-tray', daemon=True)
        self.thread.start()
        return True

    def _run(self):
        try:
            self.icon.run()
        except Exception as exc:
            log.debug('托盘运行异常：%s', exc)

    def _on_open(self, icon=None, item=None):
        self.window.queue.put(('open',))

    def _on_browser(self, icon=None, item=None):
        self.window.queue.put(('open_browser',))

    def _on_copy(self, icon=None, item=None):
        self.window.queue.put(('copy_url',))

    def _on_quit(self, icon=None, item=None):
        self.window.queue.put(('quit',))

    def notify(self, text):
        try:
            if self.icon:
                self.icon.notify(text, 'LanShare')
        except Exception:
            pass

    def stop(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
