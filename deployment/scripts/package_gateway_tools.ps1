# Builds the Gateway tools Lambda zip (OpsResolve-target-tools-lambda) into build\gateway\ (local only, no AWS calls).
# Holds only lambda_handler.py at the zip root. boto3 comes from the Python 3.14 runtime.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = Split-Path -Parent $PSScriptRoot
$out  = Join-Path $root "build\gateway"
New-Item -ItemType Directory -Force $out | Out-Null
$zipPath = Join-Path $out "gateway-tools.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath }
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, "Create")
try {
    $file = Join-Path $root "gateway tools\lambda_handler.py"
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file, "lambda_handler.py", "Optimal")
} finally { $zip.Dispose() }
"{0}  {1} bytes" -f $zipPath, (Get-Item $zipPath).Length
