"""Smoke-import every packaged Lambda zip with ONLY that zip on sys.path (no repo source), to catch a missing module
before it reaches AWS ("No module named 'opsresolve....'"). Also checks entry names use forward slashes.

  python scripts/check_zips.py            # after the package_* scripts have built the zips under build/

boto3 comes from the local environment (Lambda runtimes ship it). Nothing is called: the handler modules are only
imported. The agent-invoker zip is checked for entry names and the presence of its handler module, not imported
(its dependencies are Linux wheels).
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
IMPORTABLE = {  # zip -> module the Lambda handler points at
    BUILD / "console_api" / "console-read.zip": "opsresolve.console_api.read_handler",
    BUILD / "console_api" / "console-approval-decision.zip": "opsresolve.console_api.approval_decision_handler",
    BUILD / "console_api" / "console-scenario.zip": "opsresolve.console_api.scenario_handler",
    BUILD / "inbound" / "inbound-normalizer.zip": "opsresolve.inbound.normalizer",
    BUILD / "mobile" / "field-read.zip": "opsresolve.field_api.field_read",
    BUILD / "mobile" / "field-actions.zip": "opsresolve.field_api.field_actions",
    BUILD / "mobile" / "field-uploads.zip": "opsresolve.field_api.field_uploads",
    BUILD / "mobile" / "field-assistant.zip": "opsresolve.field_api.field_assistant",
    BUILD / "mobile" / "availability-slots.zip": "lambda_function",
}


def _names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def main() -> int:
    failed = 0
    for path, module in IMPORTABLE.items():
        if not path.exists():
            print(f"MISSING {path.relative_to(ROOT)} (build it first)")
            failed += 1
            continue
        if any("\\" in n for n in _names(path)):
            print(f"FAIL {path.name}: backslash entry names")
            failed += 1
            continue
        code = f"import sys; sys.path.insert(0, {str(path)!r}); import {module}"
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=str(BUILD))
        if r.returncode:
            print(f"FAIL {path.name}: {r.stderr.strip().splitlines()[-1]}")
            failed += 1
        else:
            print(f"ok   {path.name} imports {module}")
    inv = BUILD / "deploy" / "agent-invoker.zip"
    if inv.exists():
        names = _names(inv)
        bad = any("\\" in n for n in names) or "opsresolve/runtime/handler.py" not in names \
            or "opsresolve/common/tenants.py" not in names or "config/secrets.json" in names
        print(("FAIL " if bad else "ok   ") + f"agent-invoker.zip ({len(names)} entries): slashes, handler, tenants.py, no secrets")
        failed += bad
    else:
        print("MISSING build/deploy/agent-invoker.zip")
        failed += 1
    gw = BUILD / "gateway" / "gateway-tools.zip"
    if gw.exists():
        ok = "lambda_handler.py" in _names(gw) and not any("\\" in n for n in _names(gw))
        print(("ok   " if ok else "FAIL ") + "gateway-tools.zip: lambda_handler.py at the root, forward slashes")
        failed += not ok
    else:
        print("MISSING build/gateway/gateway-tools.zip")
        failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
