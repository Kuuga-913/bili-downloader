@echo off
chcp 65001 >nul
title bilibili-downloader
set "PY=python"
set "CK="
if exist "%~dp0cookies.txt" set CK=-c "%~dp0cookies.txt"
if not "%~1"=="" (
  "%PY%" "%~dp0bilibili_dl.py" %*
  echo.
  pause
  exit /b
)
echo ============================================
echo   bilibili-downloader - Bilibili video/audio
echo   Paste a BV/av/ss/ep URL below and press Enter.
echo   Or run: run.bat "URL" [options]
echo   Options: --only video/audio/both -q 80 --audio-quality 192k --info
echo ============================================
set /p "url=Paste video URL: "
"%PY%" "%~dp0bilibili_dl.py" -u "%url%" %CK%
echo.
pause
