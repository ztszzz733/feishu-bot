@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".env" (
    echo [错误] 没有找到 .env
    echo 请先执行: copy .env.example .env
    echo 然后填写 FEISHU_APP_ID / FEISHU_APP_SECRET / LLM_API_KEY
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 没有找到 .venv，先执行:
    echo   py -3.9 -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

.venv\Scripts\python.exe bot.py
echo.
echo [已退出] 按任意键关闭
pause >nul