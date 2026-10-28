# Builds the inbound normalizer Lambda zip into build\inbound\ (local only, no AWS calls).
# Holds ONLY the opsresolve modules the handler imports. boto3 comes from the Lambda runtime.
# Entry names use forward slashes (Compress-Archive on PS 5.1 writes backslashes, which break on Linux).
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $root "src"
$out  = Join-Path $root "build\inbound"
New-Item -ItemType Directory -Force $out | Out-Null
$files = @(
    "opsresolve/__init__.py",
    "opsresolve/common/__init__.py", "opsresolve/common/config.py", "opsresolve/common/dynamo.py",
    "opsresolve/common/logging.py", "opsresolve/common/events.py", "opsresolve/common/enums.py",
    "opsresolve/common/state_machine.py", "opsresolve/common/case_store.py", "opsresolve/common/tenants.py",
    "opsresolve/engines/__init__.py", "opsresolve/engines/state_machine.py",
    "opsresolve/inbound/__init__.py", "opsresolve/inbound/postback.py", "opsresolve/inbound/normalizer.py"
)
$zipPath = Join-Path $out "inbound-normalizer.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath }
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, "Create")
try {
    foreach ($rel in $files) {
        $file = Join-Path $src ($rel -replace "/", "\")
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file, $rel, "Optimal")
    }
} finally { $zip.Dispose() }
"{0}  {1} files  {2} bytes" -f $zipPath, $files.Count, (Get-Item $zipPath).Length
