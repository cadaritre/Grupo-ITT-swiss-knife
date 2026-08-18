$ErrorActionPreference = 'Stop'
$Python = 'python'
if (Test-Path '.venv\Scripts\python.exe') { $Python = '.venv\Scripts\python.exe' }
& $Python main.py
