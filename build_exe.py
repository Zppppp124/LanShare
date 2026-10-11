# -*- coding: utf-8 -*-
"""
把 LanShare 打包成 Windows 单文件 EXE（双击即用，不带黑窗口）。

用法：
    python build_exe.py

产物：
    dist/LanShare.exe        双击启动的图形程序（无控制台窗口）
    dist/放行防火墙.bat      一次性放行防火墙（需管理员）

打包要点：
  * --onefile     ：单个 exe，方便拷到别的电脑
  * --noconsole   ：不弹黑色控制台窗口，双击像普通软件
  * --name LanShare
  * --runtime-tmpdir：把 onefile 解压目录钉死在 exe 同级的 .lanshare-tmp 下，
    避免某些目录（DLP/权限异常）导致 %TEMP% 里建不出 _MEI 目录而报
    "Could not create temporary directory!"。仍建议放纯净路径。
  * 打包后程序把配置写到 %LOCALAPPDATA%\\LanShare\\，不污染 exe 所在目录
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, 'lanshare.py')
UI = os.path.join(HERE, 'lanshare_ui.py')
GUI = os.path.join(HERE, 'lanshare_gui.py')
ICON_MOD = os.path.join(HERE, 'lanshare_icon.py')
ICON = os.path.join(HERE, 'lanshare.ico')
# 可爱字体（两款站酷字体都是 OFL 许可，可免费商用）随程序一起打包，
# 别的电脑上也能有同款字体。庆科黄油体是主字体，快乐体作兜底。
FONT_FILES = [
    os.path.join(HERE, 'assets', 'ZCOOLQingKeHuangYou-Regular.ttf'),
    os.path.join(HERE, 'assets', 'ZCOOLKuaiLe-Regular.ttf'),
]
DIST = os.path.join(HERE, 'dist')
BUILD = os.path.join(HERE, 'build')
EXE_NAME = 'LanShare'

FIREWALL_BAT = r"""@echo off
title LanShare - 放行防火墙端口
net session >nul 2>nul
if not errorlevel 1 goto ADMINOK
echo  需要管理员权限，正在请求提权...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:ADMINOK
echo ============================================================
echo    LanShare 防火墙放行
echo ============================================================
echo.
set "PORT=8765"
set /p "PORT=请输入 LanShare 使用的端口（直接回车=8765）: "
if "%PORT%"=="" set "PORT=8765"

echo.
echo  正在放行 TCP %PORT% （文件传输）...
netsh advfirewall firewall delete rule name="LanShare TCP %PORT%" >nul 2>nul
netsh advfirewall firewall add rule name="LanShare TCP %PORT%" dir=in action=allow protocol=TCP localport=%PORT% profile=private,domain >nul

echo  正在放行 UDP 48899 （设备自动发现）...
netsh advfirewall firewall delete rule name="LanShare 发现 UDP 48899" >nul 2>nul
netsh advfirewall firewall add rule name="LanShare 发现 UDP 48899" dir=in action=allow protocol=UDP localport=48899 profile=private,domain >nul

echo.
echo  完成。已放行 TCP %PORT% 和 UDP 48899（仅专用/域网络）。
echo.
pause >nul
exit /b 0
"""


def run(cmd):
    print('> ' + ' '.join(cmd))
    return subprocess.call(cmd, cwd=HERE)


def main():
    if os.name != 'nt':
        print('这个打包脚本只在 Windows 上有意义。')
        return 1
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print('没有安装 PyInstaller。请先执行：')
        print('    python -m pip install pyinstaller')
        return 1
    for f in (APP, UI, GUI, ICON_MOD):
        if not os.path.exists(f):
            print('缺少源文件：%s' % f)
            return 1

    # 图标：不存在就现场生成
    if not os.path.exists(ICON):
        try:
            sys.path.insert(0, HERE)
            from lanshare_icon import write_ico
            write_ico(ICON)
            print('已生成图标：%s' % ICON)
        except Exception as exc:
            print('生成图标失败（将不带图标打包）：%s' % exc)

    for d in (BUILD, os.path.join(DIST, EXE_NAME)):
        shutil.rmtree(d, ignore_errors=True)

    sep = ';'
    # 把运行时解压目录钉在 exe 同级的 .lanshare-tmp，绕开对 %TEMP% 的依赖。
    # 注意：PyInstaller 的 --runtime-tmpdir 不支持带空格/中文的绝对路径，
    # 这里用相对路径，bootloader 会相对 exe 所在目录解析。
    RUNTIME_TMP = '.lanshare-tmp'
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--onefile', '--noconsole',
        '--runtime-tmpdir', RUNTIME_TMP,
        '--name', EXE_NAME,
        '--distpath', DIST,
        '--workpath', BUILD,
        '--specpath', BUILD,
        '--hidden-import', 'lanshare_ui',
        '--hidden-import', 'lanshare_gui',
        '--hidden-import', 'lanshare_icon',
        '--hidden-import', 'pystray._win32',
        '--add-data', '%s%s.' % (UI, sep),
        '--add-data', '%s%s.' % (GUI, sep),
        '--add-data', '%s%s.' % (ICON_MOD, sep),
    ]
    if os.path.exists(ICON):
        cmd += ['--icon', ICON]
        cmd += ['--add-data', '%s%s.' % (ICON, sep)]
    bundled_fonts = [f for f in FONT_FILES if os.path.exists(f)]
    if bundled_fonts:
        # 放在包的 assets/ 目录下，运行时 _font_path() 会去那里找
        for f in bundled_fonts:
            cmd += ['--add-data', '%s%sassets' % (f, sep)]
            print('打包字体：%s' % os.path.basename(f))
    else:
        print('提示：没找到可爱字体，程序会回落到系统字体')
    cmd += ['--collect-submodules', 'encodings', APP]
    rc = run(cmd)
    if rc != 0:
        print('\n打包失败（退出码 %s）' % rc)
        return rc

    exe = os.path.join(DIST, EXE_NAME + '.exe')
    if not os.path.exists(exe):
        print('打包命令跑完了，但没看到 %s' % exe)
        return 1

    # 防火墙脚本一起放进 dist，方便一起拷走（GBK 编码，cmd 才能正确读中文）
    bat = os.path.join(DIST, '放行防火墙.bat')
    with open(bat, 'wb') as f:
        f.write(FIREWALL_BAT.replace('\r\n', '\n').replace('\n', '\r\n').encode('gbk'))

    # 同时放一份使用说明
    tip = os.path.join(DIST, '使用说明.txt')
    with open(tip, 'wb') as f:
        f.write((
            'LanShare —— 局域网文件互传\r\n'
            '========================================\r\n'
            '\r\n'
            '怎么用：\r\n'
            '  1. 双击 LanShare.exe，出现主窗口（不是网页）。\r\n'
            '  2. 左边选择电脑（默认是本机），右边像资源管理器一样浏览。\r\n'
            '  3. 选中文件后点「下载到…」，会让你选保存到哪个文件夹。\r\n'
            '  4. 想发给别的电脑：选中后点「发送到设备…」。\r\n'
            '\r\n'
            '关掉窗口 = 缩到右下角托盘，程序继续在后台跑。\r\n'
            '  在托盘图标上点右键可以重新打开主界面或退出程序。\r\n'
            '\r\n'
            '让几台电脑互相传文件：\r\n'
            '  每台都双击 LanShare.exe，它们会自动发现对方；\r\n'
            '  看不见就在左边点「添加设备 IP」，填对方的 IP。\r\n'
            '\r\n'
            '开机自动启动（静默在托盘运行）：\r\n'
            '  主界面点「设置」，勾上「开机自动启动」，保存即可。\r\n'
            '\r\n'
            '连不上怎么办：\r\n'
            '  双击「放行防火墙.bat」（会请求管理员权限），\r\n'
            '  然后在 设置 - 网络和 Internet 里把网络类型改成「专用网络」。\r\n'
            '\r\n'
            '手机也能用：手机连同一个 WiFi，浏览器打开\r\n'
            '  http://这台电脑的IP:8765/  即可。\r\n'
        ).replace('\n', '\r\n').encode('utf-8-sig'))

    size = os.path.getsize(exe)
    print('\n' + '=' * 60)
    print('打包完成：%s（%.1f MB）' % (exe, size / 1024.0 / 1024.0))
    print('同目录还生成了：放行防火墙.bat、使用说明.txt')
    print('把 dist 里的文件拷到别的电脑，双击 LanShare.exe 就能用。')
    print('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
