"""Build build/deploy/agent-invoker.zip for opsresolve-dev-agent-invoker (python3.12, x86_64).

Lean zip: third-party dependencies (with their *.dist-info) at the zip root, plus
opsresolve/** (from src/), config/demo.yaml and config/ssm_parameters.json.
Never includes secrets files.

Dependency closure is taken from the LOCAL environment's exact versions, evaluating
markers for Lambda (linux / py3.12), then installed with pip --no-deps as manylinux
wheels (pip would otherwise evaluate host markers, e.g. mcp -> pywin32 on win32).

Usage (from repo root):  python scripts/package_agent_invoker.py
"""

from __future__ import annotations

import importlib.metadata as md
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DEPS_DIR = BUILD / "invoker_deps"
OUT_ZIP = BUILD / "deploy" / "agent-invoker.zip"

ROOTS = [
    "strands-agents", "mcp", "bedrock-agentcore", "pydantic",
    "jsonschema", "python-dateutil", "PyYAML", "boto3",
]

LAMBDA_ENV = {
    "sys_platform": "linux",
    "platform_system": "Linux",
    "os_name": "posix",
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "platform_machine": "x86_64",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_version": "",
}

PIP_PLATFORMS = ["manylinux2014_x86_64", "manylinux_2_28_x86_64", "manylinux_2_34_x86_64"]
SKIP_DIRS = {"__pycache__", "bin", "tests", "test"}
SKIP_SUFFIXES = (".pyc", ".pyo")


def _applies(req: Requirement, extras: set[str]) -> bool:
    if req.marker is None:
        return True
    return any(
        Marker(str(req.marker)).evaluate({**LAMBDA_ENV, "extra": e}) for e in (extras | {""})
    )


def closure() -> dict[str, str]:
    """name -> exact local version, for ROOTS and everything they need on linux/py3.12."""
    pinned: dict[str, str] = {}
    seen: set[tuple[str, frozenset]] = set()
    queue: list[tuple[str, set[str]]] = [(r, set()) for r in ROOTS]
    while queue:
        name, extras = queue.pop()
        key = (canonicalize_name(name), frozenset(extras))
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            sys.exit(f"Not installed locally (needed on Lambda): {name}")
        pinned[canonicalize_name(name)] = dist.version
        for line in dist.requires or []:
            req = Requirement(line)
            if _applies(req, extras):
                queue.append((req.name, set(req.extras)))
    return pinned


def install(pinned: dict[str, str]) -> None:
    if DEPS_DIR.exists():
        shutil.rmtree(DEPS_DIR)
    DEPS_DIR.mkdir(parents=True)
    cmd = [
        sys.executable, "-m", "pip", "install", "--quiet", "--no-deps",
        "--target", str(DEPS_DIR), "--python-version", "3.12", "--implementation", "cp",
        "--only-binary=:all:",
    ]
    for p in PIP_PLATFORMS:
        cmd += ["--platform", p]
    cmd += [f"{n}=={v}" for n, v in sorted(pinned.items())]
    subprocess.run(cmd, check=True)


def _skip(rel: Path) -> bool:
    if rel.suffix in SKIP_SUFFIXES:
        return True
    if any(part in SKIP_DIRS for part in rel.parts[:-1]):
        return True
    return _is_secrets_file(rel.as_posix())


def _is_secrets_file(name: str) -> bool:
    # basename only: botocore ships a legitimate data/secretsmanager/ directory
    return re.match(r"^secrets[^/]*\.(json|ya?ml|env|txt)$", name.rsplit("/", 1)[-1].lower()) is not None


def build_zip() -> None:
    OUT_ZIP.parent.mkdir(parents=True, exist_ok=True)
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    entries: dict[str, Path] = {}
    for f in DEPS_DIR.rglob("*"):
        rel = f.relative_to(DEPS_DIR)
        if f.is_file() and not _skip(rel) and rel.parts[0] != "bin":
            entries[rel.as_posix()] = f
    for f in (ROOT / "src" / "opsresolve").rglob("*"):
        rel = f.relative_to(ROOT / "src")
        if f.is_file() and not _skip(rel):
            entries[rel.as_posix()] = f
    for name in ("demo.yaml", "ssm_parameters.json"):
        entries[f"config/{name}"] = ROOT / "config" / name
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, f in sorted(entries.items()):
            z.write(f, arc)


def verify() -> None:
    with zipfile.ZipFile(OUT_ZIP) as z:
        names = z.namelist()
    def has(prefix: str) -> bool:
        return any(n.startswith(prefix) for n in names)
    for prefix in ("strands/", "mcp/", "bedrock_agentcore/", "mcp-", "strands_agents-",
                   "opsresolve/agents/gateway_client.py", "config/demo.yaml",
                   "config/ssm_parameters.json"):
        assert has(prefix), f"missing {prefix}"
    assert any(re.match(r"mcp-[^/]*\.dist-info/", n) for n in names), "missing mcp dist-info"
    assert any(re.match(r"strands_agents-[^/]*\.dist-info/", n) for n in names), "missing strands dist-info"
    assert not [n for n in names if _is_secrets_file(n)], "secrets file in zip"
    assert not [n for n in names if "\\" in n], "backslash in entry name"
    assert OUT_ZIP.read_bytes().count(b"opsresolve" + bytes([92])) == 0, "backslash path bytes"
    size = OUT_ZIP.stat().st_size / 1e6
    assert size < 50, f"zip too large for direct upload: {size:.1f} MB"
    print(f"OK {OUT_ZIP} {size:.1f} MB, {len(names)} entries")
    print("config/:", sorted(n for n in names if n.startswith("config/")))


if __name__ == "__main__":
    pins = closure()
    print(f"{len(pins)} packages")
    install(pins)
    build_zip()
    verify()
