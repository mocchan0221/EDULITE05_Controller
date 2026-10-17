@echo off
rem EDULITE05 Controller launcher (run_sim.bat). Works from UNC paths via pushd.
rem --sim: real core (MotorService) + SimTransport (2 motors: ID 127 and 1). Use --sim N for N motors, --demo for dummy data.
pushd "%~dp0"
if errorlevel 1 (
    echo [ERROR] pushd failed: "%~dp0"
    pause
    exit /b 1
)
set "UV_LINK_MODE=copy"
rem venv はローカルディスクに置く(共有フォルダ上だと起動・import が非常に遅いため)
if not defined UV_PROJECT_ENVIRONMENT set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\edulite05\venv"
where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv was not found.
    echo Install uv: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo or: winget install --id=astral-sh.uv -e
    popd
    pause
    exit /b 1
)
uv run --frozen python -m edulite05 --sim %*
set RC=%ERRORLEVEL%
popd
if not "%RC%"=="0" (
    echo.
    echo [ERROR] edulite05 exited with code %RC%.
    pause
)
exit /b %RC%
