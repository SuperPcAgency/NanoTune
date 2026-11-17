@echo off
title Building NanoTune...
cd /d "%~dp0"

pip install -r requirements.txt pyinstaller

if not exist "assets" mkdir "assets"
if exist "build" rmdir /s /q "build"
if exist "dist\NanoTune.exe" del /f /q "dist\NanoTune.exe"
if exist "NanoTune.spec" del /f /q "NanoTune.spec"
if exist "assets\nanotune.ico" del /f /q "assets\nanotune.ico"

echo [*] Rendering 500px HD Star icon directly from main.py...
python main.py --export-icon "%CD%\assets\nanotune.ico"
if not exist "assets\nanotune.ico" (
    echo [ERROR] Failed to generate assets\nanotune.ico!
    pause
    exit /b 1
)

echo [*] Compiling NanoTune.exe with HD Star icon...
pyinstaller --noconfirm --clean --onefile --windowed --name "NanoTune" --icon "%CD%\assets\nanotune.ico" --add-data "assets;assets" main.py

:: Refresh Windows 10 icon cache
ie4uinit.exe -show

echo.
echo Done! NanoTune.exe is in the dist\ folder.
pause