@echo off
setlocal EnableExtensions
title resolve-prep
cd /d "%~dp0"
set "HERE=%~dp0"

rem ======================================================================
rem  run.bat - sobe o resolve-prep no Windows sem precisar instalar nada
rem  na mao. Verifica Python e ffmpeg, instala o que faltar, confere a
rem  venv e inicia o programa. Pode dar dois cliques.
rem
rem  Python  - via winget (vem no Windows 10/11). Sem winget, avisa e para.
rem  ffmpeg  - usa o do PATH se houver; senao baixa a versao portatil
rem            para a pasta tools\ ao lado deste arquivo.
rem  venv   - o main.py cria e instala as dependencias sozinho; aqui so
rem            reaproveitamos a venv se ela existe e ainda funciona.
rem ======================================================================

echo [resolve-prep] verificando o ambiente...

rem ---- Python ----------------------------------------------------------
call :find_python
if defined PY_CMD goto :python_ok

echo [resolve-prep] Python nao encontrado.
where winget >nul 2>&1
if errorlevel 1 (
    echo   O winget nao esta disponivel, entao nao consigo instalar sozinho.
    echo   Baixe o Python em https://www.python.org/downloads/windows/
    echo   marque "Add python.exe to PATH" na instalacao e rode este arquivo de novo.
    pause
    exit /b 1
)
echo [resolve-prep] instalando Python via winget, pode levar alguns minutos...
winget install --id Python.Python.3.13 -e --source winget --accept-package-agreements --accept-source-agreements
call :refresh_path
call :find_python
if not defined PY_CMD (
    echo [resolve-prep] A instalacao terminou mas o Python ainda nao apareceu.
    echo   Feche esta janela e rode o run.bat de novo.
    pause
    exit /b 1
)

:python_ok
for /f "tokens=2" %%v in ('"%PY_CMD% --version"') do set "PY_VER=%%v"
echo [resolve-prep] Python %PY_VER%

rem ---- ffmpeg ----------------------------------------------------------
call :find_ffmpeg
if defined FFMPEG_OK goto :ffmpeg_ok

echo [resolve-prep] ffmpeg nao encontrado, baixando a versao portatil, sao uns 90 MB...
if not exist "tools" mkdir "tools"
curl -L --fail --progress-bar -o "tools\ffmpeg.zip" "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
if errorlevel 1 (
    echo [resolve-prep] O download falhou. Verifique a conexao e tente de novo,
    echo   ou instale manualmente: winget install Gyan.FFmpeg
    pause
    exit /b 1
)
tar -xf "tools\ffmpeg.zip" -C "tools"
if errorlevel 1 (
    echo [resolve-prep] Nao consegui extrair o ffmpeg.
    pause
    exit /b 1
)
del /q "tools\ffmpeg.zip"
call :find_ffmpeg
if not defined FFMPEG_OK (
    echo [resolve-prep] Baixei o ffmpeg mas nao achei o ffmpeg.exe dentro de tools\.
    pause
    exit /b 1
)

:ffmpeg_ok
echo [resolve-prep] ffmpeg ok

rem ---- venv ------------------------------------------------------------
set "VENV_PY=venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys" >nul 2>&1 || (
        echo [resolve-prep] a venv esta quebrada, recriando...
        rmdir /s /q "venv"
    )
)

echo.
if exist "%VENV_PY%" (
    "%VENV_PY%" main.py %*
) else (
    %PY_CMD% main.py %*
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [resolve-prep] o programa terminou com erro, codigo %RC%.
    pause
)
exit /b %RC%

rem ======================================================================
rem  funcoes
rem ======================================================================

:find_python
rem Deixa PY_CMD com o comando que roda um Python 3.9+ ou vazio.
set "PY_CMD="
call :try_python py -3
if defined PY_CMD goto :eof
call :try_python python
if defined PY_CMD goto :eof
rem Recem-instalado e ainda fora do PATH desta janela: procura nas pastas padrao.
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do (
    if not defined PY_CMD if exist "%%~d\python.exe" call :try_python "%%~d\python.exe"
)
goto :eof

:try_python
rem O stub da Microsoft Store e o "py" sem Python 3 falham aqui, como deve ser.
%* -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PY_CMD=%*"
goto :eof

:find_ffmpeg
rem Deixa FFMPEG_OK=1 se ffmpeg e ffprobe estao acessiveis, colocando a
rem copia portatil de tools\ no PATH desta janela se ela existir.
set "FFMPEG_OK="
for /d %%b in ("%HERE%tools\ffmpeg-*") do if exist "%%~b\bin\ffmpeg.exe" set "PATH=%%~b\bin;%PATH%"
where ffmpeg >nul 2>&1 && where ffprobe >nul 2>&1 && set "FFMPEG_OK=1"
goto :eof

:refresh_path
rem O instalador grava no PATH do registro, mas esta janela ainda tem o antigo.
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%p;%PATH%"
goto :eof
