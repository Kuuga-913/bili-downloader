@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe "gui\bili_downloader_launcher.pyw"
) else (
  start "" "gui\bili_downloader_launcher.pyw"
)
