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
  --collect-all laspy `
  --collect-all lazrs `
  --collect-all pye57 `
  --collect-all tkinterdnd2 `
  --exclude-module open3d `
  --collect-submodules photo_report_app `
  main.py
Write-Host 'EXE creado en dist\HerramientasGrupoITT.exe'

& $Python -m PyInstaller --noconfirm --clean --windowed --onefile `
  --name 'Visor3DGrupoITT' `
  --icon 'assets\logo.ico' `
  --collect-data open3d `
  --collect-binaries open3d `
  --hidden-import open3d.cpu.pybind `
  --exclude-module open3d.ml `
  --exclude-module open3d.examples `
  --exclude-module open3d.web_visualizer `
  --exclude-module open3d.visualization.tensorboard_plugin `
  --exclude-module dash `
  --exclude-module flask `
  --exclude-module IPython `
  --exclude-module ipywidgets `
  --exclude-module nbformat `
  photo_report_app\pointcloud_viewer_entry.py
Write-Host 'Visor opcional creado en dist\Visor3DGrupoITT.exe'
