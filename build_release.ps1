$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Select-String -Path (Join-Path $here 'frpc\app.py') -Pattern '^app_version = "(.+)"').Matches[0].Groups[1].Value
$name = "FruityRPC-$version"
$staging = Join-Path $env:TEMP $name
$zip = Join-Path $here "dist\$name.zip"

# always package freshly built executables
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here 'build_exe.ps1')
if ($LASTEXITCODE -ne 0) { throw 'building the executables failed' }

if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Force $staging | Out-Null
New-Item -ItemType Directory -Force (Join-Path $here 'dist') | Out-Null

# the standalone build: no Python, no sources, no build scripts
$files = @(
    'FruityRPC.exe',
    'FruityRPC-Console.exe',
    'Setup.bat',
    'Diagnose.bat',
    'Stop-FruityRPC.bat'
)
foreach ($file in $files) {
    Copy-Item (Join-Path $here $file) $staging
}

# art to upload to the Discord application
Copy-Item (Join-Path $here 'assets') (Join-Path $staging 'assets') -Recurse

# nothing personal, nothing generated
Get-ChildItem $staging -Recurse -Force -Include '__pycache__' -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $staging -Recurse -File -Include '*.pyc', '*.log', '*.previous',
    '*.broken', '*.converted', '*.md', '*.cs', 'state.json', 'config.json',
    'config.yml', 'assets.json' |
    Remove-Item -Force -ErrorAction SilentlyContinue

if (Test-Path $zip) { Remove-Item $zip -Force }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::Open($zip, 'Create')
try {
    Push-Location $staging
    foreach ($item in Get-ChildItem $staging -Recurse -File) {
        $relative = (Resolve-Path -Relative $item.FullName).TrimStart('.', '\')
        $entry = $name + '/' + $relative.Replace('\', '/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $item.FullName, $entry, 'Optimal') | Out-Null
    }
} finally {
    Pop-Location
    $archive.Dispose()
}

$size = [math]::Round((Get-Item $zip).Length / 1MB, 2)
Write-Host "built $zip  ($size MB)"
Remove-Item $staging -Recurse -Force
