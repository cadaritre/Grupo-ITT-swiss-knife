param(
    [string]$Version = '1.1.4'
)

$ErrorActionPreference = 'Stop'
$Workspace = (Resolve-Path $PSScriptRoot).Path
$WixCommand = Get-Command wix -ErrorAction SilentlyContinue
$WixPath = if ($WixCommand) {
    $WixCommand.Source
} else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.dotnet\tools\wix.exe'
}

if (-not (Test-Path -LiteralPath $WixPath)) {
    throw "No se encontró WiX Toolset 4. Instálalo con: dotnet tool install --global wix --version 4.0.6"
}

$WixVersion = (& $WixPath --version).Trim()
if (-not $WixVersion.StartsWith('4.')) {
    throw "Se requiere WiX Toolset 4.x y se encontró $WixVersion. Ejecuta: dotnet tool uninstall --global wix; dotnet tool install --global wix --version 4.0.6"
}

$RequiredExtensions = @('WixToolset.UI.wixext', 'WixToolset.Util.wixext')
$InstalledExtensions = (& $WixPath extension list -g) -join "`n"
foreach ($Extension in $RequiredExtensions) {
    if ($InstalledExtensions -notmatch [regex]::Escape($Extension)) {
        & $WixPath extension add -g "$Extension/4.0.6"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

& (Join-Path $Workspace 'build_exe.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$SourceExe = Join-Path $Workspace 'dist\HerramientasGrupoITT.exe'
$OutputMsi = Join-Path $Workspace 'dist\HerramientasGrupoITT.msi'
$Wxs = Join-Path $Workspace 'installer\Product.wxs'
$CustomUi = Join-Path $Workspace 'installer\CustomFeatureTree.wxs'
$Localization = Join-Path $Workspace 'installer\es-ES.wxl'
$InstallerAssets = Join-Path $Workspace 'installer\assets'

if (-not (Test-Path -LiteralPath $SourceExe)) {
    throw "No se generó el ejecutable esperado: $SourceExe"
}

& $WixPath build $Wxs $CustomUi `
    -arch x64 `
    -ext WixToolset.UI.wixext `
    -ext WixToolset.Util.wixext `
    -culture es-ES `
    -loc $Localization `
    -d "SourceExe=$SourceExe" `
    -d "ProductVersion=$Version" `
    -d "ProjectRoot=$Workspace" `
    -d "InstallerAssets=$InstallerAssets" `
    -o $OutputMsi

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "MSI creado en $OutputMsi"
