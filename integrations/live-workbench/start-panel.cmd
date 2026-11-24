@echo off
setlocal
chcp 65001 >nul
title PaperOrchestra 实时工作台
cd /d "%~dp0"

"%SystemRoot%\System32\where.exe" node >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Node.js，请先安装 Node.js 22 或更高版本。
  pause
  exit /b 1
)

if not exist "server.mjs" (
  echo [错误] 缺少 server.mjs，请确认文件夹完整。
  pause
  exit /b 1
)

if not exist "public\index.html" (
  echo [错误] 缺少 public\index.html，请确认文件夹完整。
  pause
  exit /b 1
)

set "STUDIO_EXE=%LOCALAPPDATA%\PaperOrchestra Studio\paper-orchestra-desktop.exe"
"%SystemRoot%\System32\tasklist.exe" 2>nul | "%SystemRoot%\System32\find.exe" /i "paper-orchestra-desktop" >nul
if errorlevel 1 if exist "%STUDIO_EXE%" (
  echo 正在启动 PaperOrchestra Studio...
  start "" "%STUDIO_EXE%"
  "%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul
)

echo.
echo PaperOrchestra 实时工作台正在启动...
echo 浏览器会自动打开。关闭此窗口即可停止实时服务。
echo.

node server.mjs --open
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo.
  echo [错误] 服务已退出，代码 %RESULT%
  pause
)
exit /b %RESULT%
