@echo off
set "WICKED_PYTHON=%~dp0runtime\python.exe"
if not exist "%WICKED_PYTHON%" set "WICKED_PYTHON=%USERPROFILE%\miniconda3\python.exe"
"%WICKED_PYTHON%" "%~dp0client\install_gse.py"
pause
