@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem 中文注释：此脚本只在当前用户权限下创建虚拟环境和初始化本地配置。
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 正在创建虚拟环境...
    rem 中文注释：优先使用 Windows Python 启动器；精简版 Python 安装未带 py 时回退 python 命令。
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.12 -m venv .venv 2>nul
        if errorlevel 1 py -3.11 -m venv .venv
    ) else (
        where python >nul 2>nul
        if errorlevel 1 (
            echo [错误] 未找到 Python，请安装 Python 3.11 或 3.12 并加入 PATH。
            if not defined LIANXIN_NO_PAUSE pause
            exit /b 1
        )
        python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)"
        if errorlevel 1 (
            echo [错误] 当前 python 不是受支持的 3.11 或 3.12，请安装对应版本后重试。
            if not defined LIANXIN_NO_PAUSE pause
            exit /b 1
        )
        python -m venv .venv
    )
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败。
        if not defined LIANXIN_NO_PAUSE pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
echo [2/3] 正在安装基础桌面版依赖...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查 Python 版本和网络连接。
    if not defined LIANXIN_NO_PAUSE pause
    exit /b 1
)

rem 中文注释：API Key 等个人凭据只写入用户目录，绝不写回项目目录。
set "LIANXIN_DATA=%USERPROFILE%\.lianxin"
if not exist "%LIANXIN_DATA%" mkdir "%LIANXIN_DATA%"
if not exist "%LIANXIN_DATA%\user_config.json" (
    copy /y "user_config.json.example" "%LIANXIN_DATA%\user_config.json" >nul
)

echo [3/3] 初始化完成。
echo 请在 %LIANXIN_DATA%\user_config.json 中填写自己的 API Key。
if not defined LIANXIN_NO_PAUSE pause
