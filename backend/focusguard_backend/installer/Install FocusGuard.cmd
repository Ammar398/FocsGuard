@echo off
rem FocusGuard installer - double-click to set up this computer.
rem It runs install.ps1 from this folder; open that file in Notepad to read exactly what it does.
title FocusGuard setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
