@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  WIPO 商标监控系统 - Playwright E2E 测试
echo ============================================

echo [1/3] 检查依赖...
python -c "import playwright, pytest" 2>nul
if errorlevel 1 (
    echo 安装依赖: playwright pytest pytest-html
    pip install playwright pytest pytest-html -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo [2/3] 检查 Chromium 浏览器...
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); b.close(); p.stop()" 2>nul
if errorlevel 1 (
    echo 下载 Chromium 浏览器...
    set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
    python -m playwright install chromium
)

echo [3/3] 运行测试...
python -m pytest tests -v
echo.
echo 测试报告: test-results\report.html
pause
