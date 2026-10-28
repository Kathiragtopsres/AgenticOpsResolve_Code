"""Build the Gateway tools Lambda zip (OpsResolve-target-tools-lambda) -> build/gateway/gateway-tools.zip.

Local only, no AWS calls. Supersedes package_gateway_tools.ps1: the zip now also bundles a recent
boto3/botocore, because the Python 3.14 Lambda runtime's boto3 predates managed knowledge bases
(``managedSearchConfiguration`` in bedrock-agent-runtime Retrieve) and KB TA0KF90K4I is a managed KB.

Usage (from tcs-agentic-opsresolve/):  python scripts/package_gateway_tools.py
Entry names use forward slashes (Linux Lambda); verified before the script exits.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "build" / "gateway"
DEPS = ROOT / "build" / "gateway_deps"
HANDLER = ROOT / "gateway tools" / "lambda_handler.py"
BOTO_VERSION = "1.43.102"  # first version verified with managedSearchConfiguration against KB TA0KF90K4I
MAX_DIRECT_UPLOAD_MB = 50


def main() -> None:
    if DEPS.exists():
        shutil.rmtree(DEPS)
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(DEPS),
                    f"boto3=={BOTO_VERSION}", f"botocore=={BOTO_VERSION}"], check=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUT_DIR / "gateway-tools.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(HANDLER, "lambda_handler.py")
        for f in sorted(DEPS.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc" and "bin" not in f.relative_to(DEPS).parts[:1]:
                z.write(f, f.relative_to(DEPS).as_posix())
    data = zip_path.read_bytes()
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    assert b"\\" not in b"".join(n.encode() for n in names), "backslash entry names"
    assert "lambda_handler.py" in names and any(n.startswith("botocore/") for n in names)
    size_mb = len(data) / 1e6
    assert size_mb < MAX_DIRECT_UPLOAD_MB, f"{size_mb:.1f} MB: too large for a direct upload"
    print(f"{zip_path}  {len(names)} files  {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
