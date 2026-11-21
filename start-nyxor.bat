@echo off
title NYXOR
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (echo NYXOR needs Python 3.11 or newer. & pause & exit /b 1)
%PY% server.py %*
pause
