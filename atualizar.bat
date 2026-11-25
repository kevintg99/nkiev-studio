@echo off
chcp 65001 >nul
rem Atualiza o bot (git pull) sem travar por causa de ajustes feitos a mao no config.yaml.
cd /d "%~dp0"
echo.
echo  Atualizando o nKiev Studio...
echo.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -m studio.atualizar
git pull
if errorlevel 1 (
  echo.
  echo  A atualizacao falhou. Copie a mensagem acima e mande para o suporte.
  pause
  exit /b 1
)
"%PY%" -m pip install -q -r requirements.txt
rem placa NVIDIA: torch com CUDA e bibliotecas da transcricao (sem placa, nao faz nada)
"%PY%" -m studio.gpu
where deno >nul 2>nul
if errorlevel 1 winget install --id=DenoLand.Deno -e --accept-source-agreements --accept-package-agreements >nul 2>nul
echo.
echo  Pronto! Feche e abra o nKiev Studio de novo.
echo.
pause
