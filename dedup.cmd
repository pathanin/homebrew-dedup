@echo off
REM Try py launcher first (recommended), fall back to python
where /q py.exe 2>nul
if not errorlevel 1 (
    py -3 -B "%~dp0dedup.py" %*
) else (
    python -B "%~dp0dedup.py" %*
)
exit /b %ERRORLEVEL%
