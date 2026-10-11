# -*- coding: utf-8 -*-
"""
生成 Windows 批处理脚本（GBK/936 编码）。

为什么需要这个脚本：
  cmd.exe 按「当前代码页」（中文 Windows 默认 GBK/936）逐行读取 .bat 文件。
  如果 .bat 以 UTF-8 保存，中文字节会被按 GBK 误解，导致命令行被切碎、
  出现 "'oads' 不是内部或外部命令" 之类的错误；而文件里的 chcp 65001
  又会改变字节偏移，进一步破坏后续行的解析。
  因此 .bat 必须用 GBK 保存，且不要在文件中间切换代码页。

用法：python build_bats.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENCODING = 'gbk'

START_BAT = r"""@echo off
title LanShare - 局域网文件互传
rem 用 "%~dp0." 进入目录：直接写 "%~dp0" 末尾有反斜杠，
rem 放进引号里会把右引号转义掉（"...\工具\"），导致参数被拼坏。
cd /d "%~dp0."

echo ============================================================
echo    LanShare 局域网文件互传工具
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 goto NOPYTHON

echo  请选择要共享的目录：
echo.
echo    1. 当前目录（本工具所在文件夹）
echo    2. 自定义目录（手动输入路径）
echo    3. 我的「下载」文件夹
echo.
goto PICK

:PICK
set "SHARED=%CD%"
set /p "choice=请输入 1 / 2 / 3 然后回车（直接回车=1）: "
if "%choice%"=="2" goto ASK
if "%choice%"=="3" goto DOWNLOADS
goto GO

:ASK
echo.
set /p "SHARED=把要共享的文件夹路径粘贴到这里，然后回车: "
rem 去掉用户可能粘贴进来的尾部反斜杠，避免引号被转义
if "%SHARED:~-1%"=="\" set "SHARED=%SHARED:~0,-1%"
goto GO

:DOWNLOADS
set "SHARED=%USERPROFILE%\Downloads"
goto GO

:GO
if not exist "%SHARED%" goto NODIR

echo.
echo  共享目录 : %SHARED%
echo.
echo  ------------------------------------------------------------
echo   如果局域网里还有另一台设备也运行了 LanShare，
echo   请在下面填入它的 IP 地址，两台设备就能互相找到对方。
echo   不知道就先直接回车，之后可在网页「设备」页手动添加。
echo  ------------------------------------------------------------
set "PEER="
set /p "PEER=对方设备的 IP 地址（直接回车=跳过）: "
if not "%PEER%"=="" set "PEER=--peer %PEER%"

echo.
echo  正在启动服务，稍等几秒...
echo.
echo  ------------------------------------------------------------
python "%~dp0lanshare.py" --root "%SHARED%" --open %PEER%
echo  ------------------------------------------------------------
echo.
echo  服务已停止。按任意键关闭窗口。
pause >nul
exit /b 0

:NOPYTHON
echo.
echo  [错误] 没有找到 Python。
echo.
echo  请先安装 Python 3.8 或更高版本：https://www.python.org/downloads/
echo  安装时务必勾选底部的 "Add Python to PATH"，装完后重新双击本文件。
echo.
echo  如果已经装过 Python 但仍报这个错，说明没勾选 PATH，
echo  可以改用下面这条命令手动运行（把路径换成你的实际安装位置）：
echo     "C:\Users\你的用户名\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0lanshare.py" --open
echo.
pause >nul
exit /b 1

:NODIR
echo.
echo  [错误] 这个目录不存在：%SHARED%
echo.
pause >nul
exit /b 1
"""

FIREWALL_BAT = r"""@echo off
title LanShare - 放行防火墙端口
cd /d "%~dp0"

net session >nul 2>nul
if not errorlevel 1 goto ADMINOK

echo  需要管理员权限才能修改防火墙规则，正在请求提权...
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
echo  如果其它设备仍连不上，请把网络类型改为「专用网络」：
echo    设置 - 网络和 Internet - 以太网/WLAN - 网络配置文件 - 专用
echo.
pause >nul
exit /b 0
"""


def write_gbk(path, text):
    data = text.replace('\r\n', '\n').replace('\n', '\r\n').encode(ENCODING)
    with open(path, 'wb') as f:
        f.write(data)
    return len(data)


def main():
    targets = [('启动-LanShare.bat', START_BAT), ('放行防火墙.bat', FIREWALL_BAT)]
    for name, text in targets:
        path = os.path.join(HERE, name)
        size = write_gbk(path, text)
        # 自检：必须能用 GBK 解回来，且不含 UTF-8 BOM
        with open(path, 'rb') as f:
            raw = f.read()
        assert raw[:3] != b'\xef\xbb\xbf', '%s 不应带 BOM' % name
        raw.decode(ENCODING)
        print('已生成 %s（%d 字节，%s 编码）' % (name, size, ENCODING.upper()))
    print('提示：修改这两个脚本请改本文件，然后重新运行 python build_bats.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
