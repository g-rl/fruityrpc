$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$work = Join-Path $env:TEMP 'fruityrpc-build'
$icon = Join-Path $here 'assets\fl_logo.ico'

if (Test-Path $work) { Remove-Item $work -Recurse -Force }
$stage = $work + '-bridge'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $stage 'fl-script') | Out-Null

# the bridge script travels inside the exe, without its comments
$bridge_source = Join-Path $stage 'fl-script\device_FruityRPC.py'
Copy-Item (Join-Path $here 'fl-script\device_FruityRPC.py') $bridge_source
& py -3 (Join-Path $here 'tools\strip_py.py') (Join-Path $stage 'fl-script')
if ($LASTEXITCODE -ne 0) { throw 'stripping the bridge script failed' }
$bridge = $bridge_source + ';fl-script'

$common = @(
    '--onefile',
    '--noconfirm',
    '--clean',
    '--optimize', '2',
    '--distpath', $here,
    '--workpath', $work,
    '--specpath', $work,
    '--icon', $icon,
    '--add-data', $bridge,
    '--exclude-module', 'tkinter',
    '--exclude-module', 'unittest',
    '--exclude-module', 'pydoc'
)

# the daemon: no console window, this is what FL Studio launches
& py -3 -m PyInstaller @common '--windowed' '--name' 'FruityRPC' (Join-Path $here 'fruityrpc.py')
if ($LASTEXITCODE -ne 0) { throw 'building FruityRPC.exe failed' }

# same program with a console, for --setup and --diagnose
& py -3 -m PyInstaller @common '--console' '--name' 'FruityRPC-Console' (Join-Path $here 'fruityrpc.py')
if ($LASTEXITCODE -ne 0) { throw 'building FruityRPC-Console.exe failed' }

Remove-Item $work -Recurse -Force
Remove-Item $stage -Recurse -Force
foreach ($name in 'FruityRPC.exe', 'FruityRPC-Console.exe') {
    $file = Join-Path $here $name
    $size = [math]::Round((Get-Item $file).Length / 1MB, 2)
    Write-Host "built $file  ($size MB)"
}
