@echo off
rem cdx - visual cd. "cdx" or "cd" with no arguments opens the browser;
rem "cd <path>" behaves exactly like the normal cd.
rem In the browser, Ctrl+Enter on a .bat file cd's to its folder and runs it.
rem No setlocal on purpose: the cd has to stick in the calling cmd window.

if not "%~1"=="" goto :passthru

set "CDX_OUT=%TEMP%\cdx_%RANDOM%%RANDOM%.tmp"
set "CDX_PY=python"
where py >nul 2>nul && set "CDX_PY=py -3"

%CDX_PY% "%~dp0cdx.py" --out "%CDX_OUT%"

set "CDX_DEST="
set "CDX_RUN="
if exist "%CDX_OUT%" set /p CDX_DEST=<"%CDX_OUT%"
if exist "%CDX_OUT%.run" set /p CDX_RUN=<"%CDX_OUT%.run"
if exist "%CDX_OUT%" del "%CDX_OUT%" >nul 2>nul
if exist "%CDX_OUT%.run" del "%CDX_OUT%.run" >nul 2>nul
set "CDX_OUT="
set "CDX_PY="
if not defined CDX_DEST goto :eof
cd /d "%CDX_DEST%"
set "CDX_DEST="
if not defined CDX_RUN goto :eof
rem the loop variable lets CDX_RUN be cleared before the .bat starts
for %%R in ("%CDX_RUN%") do (
    set "CDX_RUN="
    call ".\%%~R"
)
goto :eof

:passthru
if /i "%~1"=="/d" goto :plain
cd /d %*
goto :eof

:plain
cd %*
goto :eof
