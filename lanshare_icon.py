# -*- coding: utf-8 -*-
"""
图标生成：用纯 Python 画出一个 ICO（不依赖 Pillow）。

图标含义：蓝色圆角方块 + 一上一下两个白色箭头（表示「互传」）。
用 4x4 超采样做抗锯齿，效果足够在托盘和任务栏里清晰显示。
"""

import os
import struct

# 主题色
BG_TOP = (0x4B, 0x8B, 0xFF)
BG_BOTTOM = (0x2A, 0x55, 0xD8)
FG = (0xFF, 0xFF, 0xFF)


def _rounded_rect_coverage(x, y, size, radius, inset):
    """点 (x,y) 是否落在圆角矩形内。"""
    half = size / 2.0 - inset
    cx, cy = size / 2.0, size / 2.0
    dx = abs(x - cx) - (half - radius)
    dy = abs(y - cy) - (half - radius)
    if dx <= 0 and dy <= 0:
        return True
    dx = max(dx, 0.0)
    dy = max(dy, 0.0)
    return (dx * dx + dy * dy) <= radius * radius


def _arrow_coverage(x, y, size, up=True):
    """一个简洁的箭头（三角 + 直杆）。"""
    u = x / float(size)
    v = (size - y) / float(size) if up else y / float(size)
    # 三角部分：从尖端往下张开
    if 0.18 <= v <= 0.52:
        t = (v - 0.18) / 0.34
        halfw = 0.055 + t * 0.185
        if abs(u - 0.5) <= halfw:
            return True
    # 杆部
    if 0.50 <= v <= 0.84:
        if abs(u - 0.5) <= 0.062:
            return True
    return False


def render_rgba(size, ss=4):
    """渲染出 RGBA 像素表：返回 bytearray，逐像素 (r,g,b,a) 行优先（自下而上由调用方处理）。"""
    px = bytearray(size * size * 4)
    step = 1.0 / ss
    inv = 1.0 / (ss * ss)
    radius = size * 0.22
    inset = max(1.0, size * 0.045)
    for y in range(size):
        for x in range(size):
            bg_hit = 0
            fg_hit = 0
            for sy in range(ss):
                for sx in range(ss):
                    fx = x + (sx + 0.5) * step
                    fy = y + (sy + 0.5) * step
                    if _rounded_rect_coverage(fx, fy, size, radius, inset):
                        bg_hit += 1
                        if _arrow_coverage(fx, fy, size, up=True) or \
                           _arrow_coverage(fx, fy, size, up=False):
                            fg_hit += 1
            if not bg_hit:
                continue
            # 背景竖直渐变
            t = y / float(max(1, size - 1))
            br = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
            bgc = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
            bb = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
            fa = fg_hit * inv
            r = int(br + (FG[0] - br) * fa)
            g = int(bgc + (FG[1] - bgc) * fa)
            b = int(bb + (FG[2] - bb) * fa)
            a = int(255 * bg_hit * inv)
            i = (y * size + x) * 4
            px[i] = r
            px[i + 1] = g
            px[i + 2] = b
            px[i + 3] = a
    return px


def make_ico_bytes(sizes=(16, 24, 32, 48, 64, 128, 256)):
    """生成一个多尺寸 ICO 的字节内容。"""
    images = []
    for size in sizes:
        rgba = render_rgba(size)
        images.append((size, _bmp_from_rgba(size, rgba)))
    count = len(images)
    header = struct.pack('<HHH', 0, 1, count)
    offset = 6 + 16 * count
    entries = b''
    body = b''
    for size, data in images:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries += struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        body += data
    return header + entries + body


def _bmp_from_rgba(size, rgba):
    """把 RGBA 像素打包成 ICO 内部使用的 BMP（含 AND 掩码）。"""
    # BITMAPINFOHEADER：高度要写两倍（XOR 图 + AND 掩码）
    header = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(size - 1, -1, -1):          # BMP 自下而上
        row = rgba[y * size * 4:(y + 1) * size * 4]
        for x in range(size):
            i = x * 4
            xor += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))  # BGRA
    # AND 掩码：1 = 透明，每行按 4 字节对齐
    row_bytes = ((size + 31) // 32) * 4
    andmask = bytearray(row_bytes * size)
    for y in range(size):
        for x in range(size):
            a = rgba[((size - 1 - y) * size + x) * 4 + 3]
            if a < 128:
                andmask[y * row_bytes + (x // 8)] |= (0x80 >> (x % 8))
    return bytes(header) + bytes(xor) + bytes(andmask)


def write_ico(path):
    data = make_ico_bytes()
    with open(path, 'wb') as f:
        f.write(data)
    return path


if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, 'lanshare.ico')
    write_ico(out)
    print('已生成 %s（%d 字节）' % (out, os.path.getsize(out)))
