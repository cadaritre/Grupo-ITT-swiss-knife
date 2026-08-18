$ErrorActionPreference = 'Stop'
$Python = 'python'
if (Test-Path '.venv\Scripts\python.exe') { $Python = '.venv\Scripts\python.exe' }
& $Python -m pip install -r requirements.txt
& $Python -m PyInstaller --noconfirm --clean --windowed --onefile `
  --name 'HerramientasGrupoITT' `
  --icon 'assets\logo.ico' `
  --add-data 'assets;assets' `
  --collect-all reportlab `
  --collect-all pyproj `
  --collect-all spellchecker `
  --collect-all wordfreq `
  --collect-submodules photo_report_app `
  main.py
Write-Host 'EXE creado en dist\HerramientasGrupoITT.exe'
