@echo off
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
