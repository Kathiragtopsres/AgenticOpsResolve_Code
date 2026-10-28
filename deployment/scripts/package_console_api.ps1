# Builds 3 pure-python Lambda zips into build\console_api\ (local only, no AWS calls).
# Each zip holds ONLY the opsresolve modules its handler imports. boto3 comes from the Lambda runtime.
# Entry names use forward slashes (Compress-Archive on PS 5.1 writes backslashes, which break on Linux).
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $root "src"
$out  = Join-Path $root "build\console_api"
New-Item -ItemType Directory -Force $out | Out-Null

$base = @("opsresolve/__init__.py", "opsresolve/common/__init__.py", "opsresolve/common/config.py",
          "opsresolve/common/logging.py", "opsresolve/common/dynamo.py", "opsresolve/common/tenants.py",
          "opsresolve/console_api/__init__.py", "opsresolve/console_api/_common.py")
$zips = [ordered]@{
    "console-read"              = $base + @("opsresolve/console_api/read_handler.py")
    "console-approval-decision" = $base + @("opsresolve/common/events.py", "opsresolve/console_api/approval_decision_handler.py")
    "console-scenario"          = $base + @("opsresolve/runtime/__init__.py", "opsresolve/runtime/normalize.py",
                                            "opsresolve/console_api/scenario_handler.py", "opsresolve/console_api/scenario_seed.py")
}

foreach ($name in $zips.Keys) {
    $zipPath = Join-Path $out "$name.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, "Create")
    try {
        foreach ($rel in $zips[$name]) {
            $file = Join-Path $src ($rel -replace "/", "\")
            [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file, $rel, "Optimal")
        }
    } finally { $zip.Dispose() }
    "{0}  {1} files  {2} bytes" -f $zipPath, $zips[$name].Count, (Get-Item $zipPath).Length
}
