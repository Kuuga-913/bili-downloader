@echo off
chcp 65001 >nul
title video_spider
if "%~1"=="" (
  echo ============================================
  echo   video_spider - video downloader
  echo   Paste a video URL below and press Enter.
  echo   Or run with args: run.bat "URL" [options]
  echo ============================================
  set /p url=Paste video URL: 
  python "%~dp0spider.py" "%url%"
) else (
  python "%~dp0spider.py" %*
)
echo.
pause
