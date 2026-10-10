@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem ===========================================================================
rem  莲心 AI · 新版界面一键启动（React + TypeScript + Vite + Tauri 2）
rem
rem  设计原则：所有路径都以本脚本所在目录（%~dp0）为基准解析，
rem  不写死任何盘符或用户名，因此整个项目文件夹可以随意移动、改名、换盘。
rem
rem  本脚本只启动新版 Tauri 界面；旧版 PyQt5 主界面请用 python main.py。
rem  本脚本刻意不使用 goto（UTF-8 批处理里 goto 会触发字节错位），
rem  统一用 FAIL 标记收集错误后在结尾一次性退出。
rem
rem  自动化/调试：设置 LIANXIN_DRYRUN=1 只做依赖体检、不真正启动；
rem              设置 LIANXIN_NO_PAUSE=1 跳过结尾的 pause。
rem ===========================================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "LOG=%ROOT%startup_log.txt"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
set "FAIL="

echo ============================================
echo   莲心 AI · 新版界面（React + Tauri 2）
echo ============================================
echo 项目目录: %ROOT%
echo.

rem ── 0. 目录完整性 ─────────────────────────────────────────────────────
if not exist "%ROOT%api_server.py" (
    echo [错误] 当前目录下没有 api_server.py。
    echo        请把 run.bat 放在莲心项目根目录下再运行。
    set "FAIL=1"
)
if not exist "%ROOT%frontend\package.json" (
    echo [错误] 没有找到 frontend\package.json，frontend 目录不完整。
    set "FAIL=1"
)
if not exist "%ROOT%frontend\start_ui.cmd" (
    echo [错误] 没有找到 frontend\start_ui.cmd，无法拉起界面窗口。
    set "FAIL=1"
)

rem ── 1. 找一个可用的 Python 解释器 ──────────────────────────────────────
set "PY="
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if not defined PY if defined LIANXIN_PYTHON if exist "%LIANXIN_PYTHON%" set "PY=%LIANXIN_PYTHON%"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [错误] 没有找到 Python 解释器，以下任选一种：
    echo        1^) 先运行同目录的 bootstrap.bat 创建项目内 .venv；
    echo        2^) 设置环境变量 LIANXIN_PYTHON 指向你的 python.exe；
    echo        3^) 把 Python 加入系统 PATH。
    set "FAIL=1"
)

rem ── 2. 检查 Node.js / npm（Vite 与 Tauri CLI 需要）────────────────────
where node >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 Node.js。新版界面需要 Node.js 18 或更高版本。
    echo        下载地址: https://nodejs.org/
    set "FAIL=1"
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 npm（通常随 Node.js 一起安装，请检查 PATH）。
    set "FAIL=1"
)

rem ── 3. 检查 Rust / Cargo（Tauri 2 编译桌面窗口需要）──────────────────
where cargo >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 Rust 工具链 cargo，Tauri 2 需要它来编译桌面窗口。
    echo        安装地址: https://rustup.rs/   安装后重新运行本脚本。
    set "FAIL=1"
)

if defined FAIL (
    echo.
    echo [中断] 运行环境不完整，请按上面的提示安装后重新运行本脚本。
    if not defined LIANXIN_NO_PAUSE pause
    exit /b 1
)

echo [1/4] 依赖检查通过
echo        Python : %PY%
echo        Node   :
node -v
echo        Cargo  :
cargo -V
echo.

rem ── 4. 前端依赖（首次运行自动安装）────────────────────────────────────
if exist "%ROOT%frontend\node_modules" (
    echo [2/4] 前端依赖已存在，跳过 npm install。
) else (
    echo [2/4] 首次运行：正在安装前端依赖 ^(npm install^)，可能需要几分钟...
    pushd "%ROOT%frontend"
    call npm install
    if errorlevel 1 set "FAIL=npm"
    popd
)

if defined FAIL (
    echo [错误] npm install 失败，请检查网络后重试，或在 frontend 目录手动执行 npm install。
    if not defined LIANXIN_NO_PAUSE pause
    exit /b 1
)

if defined LIANXIN_DRYRUN (
    echo [dry-run] 依赖体检完成，未启动任何进程。
    exit /b 0
)

rem ── 5. 后端接口 api_server.py ─────────────────────────────────────────
netstat -ano | findstr /c:":8766 " >nul 2>nul
if not errorlevel 1 (
    echo [3/4] 8766 端口已有服务在运行，直接复用现有后端。
) else (
    echo [3/4] 启动后端接口 api_server.py ^(http://127.0.0.1:8766^)...
    echo [%date% %time%] 新版界面启动：拉起 api_server>> "%LOG%"
    start "莲心 AI 后端 8766" /d "%ROOT%" cmd /k ""%PY%" api_server.py"
)

netstat -ano | findstr /c:":5173 " >nul 2>nul
if not errorlevel 1 (
    echo [提示] 5173 端口已被占用，可能已经有一个界面在运行。
    echo        请先关闭那个窗口，否则新窗口可能立即退出。
)

rem ── 6. 新版界面窗口 ───────────────────────────────────────────────────
echo [4/4] 启动新版界面窗口 ^(首次编译 Rust 需要几分钟，请耐心等待^)...
echo [%date% %time%] 新版界面启动：拉起 tauri dev>> "%LOG%"
start "莲心 AI 界面 Tauri" "%ROOT%frontend\start_ui.cmd"

echo.
echo ============================================
echo   已启动
echo   - 后端窗口：莲心 AI 后端 8766
echo   - 界面窗口：莲心 AI 界面 Tauri（关闭它即退出应用）
echo   - 启动记录：%LOG%
echo   想用旧版 PyQt5 主界面：python main.py
echo ============================================
echo 本窗口可以关闭。
if not defined LIANXIN_NO_PAUSE pause
exit /b 0
