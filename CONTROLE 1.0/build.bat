@echo off
REM Gera PAJM_Controle.exe.  Basta dar duplo clique neste arquivo.
REM
REM O ambiente virtual fica FORA do OneDrive de proposito: o OneDrive trava
REM arquivos durante o build e o "Arquivos Sob Demanda" pode desmaterializar
REM bibliotecas no meio de uma execucao.

setlocal
REM Um ambiente por usuario do Windows, na pasta dele (fora do OneDrive)
set VENV=%USERPROFILE%\.venvs\pajm
set PY=%VENV%\Scripts\python.exe

if not exist "%PY%" (
    echo.
    echo ERRO: nao encontrei o ambiente virtual em %VENV%
    echo Rode:  python -m venv %VENV%
    echo E depois: "%PY%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"

echo === Rodando os testes antes de empacotar ===
REM Todos os conjuntos, sem excecao. Se um falhar, o .exe NAO e atualizado.
for %%T in (
    teste_fatiador
    teste_job
    teste_padroes
    teste_pacote
    teste_imagem
    teste_protocolo
    teste_sessao
    teste_envio
    teste_interface
) do (
    echo --- %%T
    "%PY%" testes\%%T.py
    if errorlevel 1 goto :falhou
)

echo.
echo === Gerando o executavel ===
REM Os artefatos vao para fora do OneDrive; so o .exe volta para ca.
"%PY%" -m PyInstaller --noconfirm ^
    --distpath "%TEMP%\pajm_build\dist" ^
    --workpath "%TEMP%\pajm_build\build" ^
    PAJM_Controle.spec
if errorlevel 1 goto :falhou

copy /Y "%TEMP%\pajm_build\dist\PAJM_Controle.exe" "%~dp0PAJM_Controle.exe" >nul
if errorlevel 1 goto :falhou

echo.
echo === Pronto ===
echo PAJM_Controle.exe gerado nesta pasta.
for %%A in ("%~dp0PAJM_Controle.exe") do echo Tamanho: %%~zA bytes
echo.
pause
exit /b 0

:falhou
echo.
echo *** O BUILD FALHOU. O executavel NAO foi atualizado. ***
echo.
pause
exit /b 1
