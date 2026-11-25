@echo off
chcp 65001 >nul
rem Instala o que o nKiev Studio precisa e cria o atalho na area de trabalho.
cd /d "%~dp0"
echo.
echo  Instalando o nKiev Studio...
echo.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo  A instalacao falhou. Confira se o Python 3.11+ esta instalado e no PATH.
  pause
  exit /b 1
)
rem placa NVIDIA: torch com CUDA e bibliotecas da transcricao (sem placa, nao faz nada)
"%PY%" -m studio.gpu
rem Deno: o yt-dlp precisa dele para baixar os videos Creative Commons do YouTube
where deno >nul 2>nul
if errorlevel 1 (
  echo  Instalando o Deno ^(para os videos do YouTube CC^)...
  winget install --id=DenoLand.Deno -e --accept-source-agreements --accept-package-agreements >nul 2>nul
  if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://deno.land/install.ps1 | iex"
)
if not exist ".env" copy ".env.example" ".env" >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'nKiev Studio.lnk')); $s.TargetPath=(Join-Path (Get-Location) 'nKiev Studio.bat'); $s.WorkingDirectory=(Get-Location).Path; $s.IconLocation=(Join-Path (Get-Location) 'assets\nkiev.ico'); $s.WindowStyle=7; $s.Save()"
echo.
echo  Pronto! Abra pelo atalho "nKiev Studio" na area de trabalho.
echo  As chaves das APIs voce coloca dentro do app, em Configuracoes.
echo.
pause
