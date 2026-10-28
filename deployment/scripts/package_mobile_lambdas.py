"""Build the five mobile technician Lambda zips -> build/mobile/<name>.zip (local only, no AWS calls).

The sources live in mobile_web_app/lambdas/<name>/ (each folder is the zip content as deployed). Entry names use forward
slashes (Linux Lambda); ``__pycache__`` / ``*.pyc`` are left out.

  python scripts/package_mobile_lambdas.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "mobile_web_app" / "lambdas"
OUT = ROOT / "build" / "mobile"
NAMES = ["field-read", "field-actions", "field-uploads", "field-assistant", "availability-slots"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        folder = SRC / name
        files = sorted(f for f in folder.rglob("*") if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc")
        path = OUT / f"{name}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f.relative_to(folder).as_posix())
        with zipfile.ZipFile(path) as z:
            assert not any("\\" in n for n in z.namelist()), f"{name}: backslash entry names"
        print(f"{path}  {len(files)} files  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
