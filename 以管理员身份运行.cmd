@echo off
set "LANSHARE_DIR=%~dp0"
powershell.exe -NoProfile -Command "Start-Process -FilePath (Join-Path $env:LANSHARE_DIR 'LANShare.exe') -WorkingDirectory $env:LANSHARE_DIR -Verb RunAs"
