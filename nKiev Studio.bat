@echo off
rem Abre o nKiev Studio (janela do app). Atalho na area de trabalho: instalar_app.bat
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m studio
) else (
  start "" pythonw -m studio
)
