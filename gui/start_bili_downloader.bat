@echo off
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe "%~dp0bili_downloader_launcher.pyw"
) else (
  start "" "%~dp0bili_downloader_launcher.pyw"
)
