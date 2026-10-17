@echo off
rem EDULITE05 Controller launcher (run.bat). Runs from the script's folder.
pushd "%~dp0"
if errorlevel 1 (
    echo [ERROR] pushd failed: "%~dp0"
    pause
    exit /b 1
)
where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv was not found.
    echo Install uv: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo or: winget install --id=astral-sh.uv -e
    popd
    pause
    exit /b 1
)
uv run --frozen python -m edulite05 %*
set RC=%ERRORLEVEL%
popd
if not "%RC%"=="0" (
    echo.
    echo [ERROR] edulite05 exited with code %RC%.
    pause
)
exit /b %RC%
