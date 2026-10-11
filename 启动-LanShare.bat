@echo off
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
