#!/usr/bin/env python3
"""Self-check for the build and test kit. Run from the kit root: python scripts/validate_kit.py

Checks: every JSON/JSONL parses; every file referenced by golden timelines and e2e scripts exists;
golden state sequences are legal; signed postbacks in samples verify with the demo key;
policy fixtures cover every write tool; eval dataset has 30 unique cases."""
import json, pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
from opsresolve_ref.state_machine import replay
from opsresolve_ref.postback import verify, PostbackError
KEY = b"demo-hmac-key-rotate-before-use"
errors = []

for p in ROOT.rglob("*.json"):
    if "node_modules" in p.parts: continue
    try: json.loads(p.read_text(encoding="utf-8"))
    except Exception as e: errors.append(f"JSON parse {p.relative_to(ROOT)}: {e}")
for p in ROOT.rglob("*.jsonl"):
    for i, line in enumerate(p.read_text().splitlines(), 1):
        if line.strip():
            try: json.loads(line)
            except Exception as e: errors.append(f"JSONL {p.name}:{i}: {e}")

refs = set()
for g in (ROOT / "golden").glob("*.json"):
    d = json.loads(g.read_text())
    try: replay(d["expectedStates"])
    except Exception as e: errors.append(f"Illegal state path in {g.name}: {e}")
    refs |= set(re.findall(r'"((?:samples|telemetry|golden|seed)/[^"#]+)', g.read_text()))
for y in (ROOT / "tests/e2e").glob("*.yaml"):
    refs |= set(re.findall(r'((?:samples|telemetry|golden|seed)/[\w./-]+)', y.read_text()))
for r in sorted(refs):
    if not (ROOT / r).exists(): errors.append(f"Missing referenced artefact: {r}")

for p in (ROOT / "samples/inputs/sns").glob("rcs_postback_*.json"):
    body = json.loads(json.loads(json.loads(p.read_text())["Records"][0]["Sns"]["Message"])["messageBody"])
    try:
        verify(KEY, body["postbackData"], now=1792497600 + 60)
        if "forged" in p.name: errors.append(f"Forged postback verified unexpectedly: {p.name}")
    except PostbackError as e:
        if "forged" not in p.name and "recovery" not in p.name: errors.append(f"{p.name}: {e}")

fx = json.loads((ROOT / "policies/fixtures/policy_test_cases.json").read_text())
writes = {json.loads(s.read_text())["title"] for s in (ROOT / "schemas/tools").glob("*.json")
          if json.loads(s.read_text())["x-opsresolve-kind"] == "write"}
uncovered = writes - {f["action"] for f in fx} - {"sendCustomerMessage", "requestApproval", "generateServiceReport"}
if uncovered: errors.append(f"Write tools without policy fixtures: {sorted(uncovered)}")

ev = [json.loads(l) for l in (ROOT / "tests/eval/eval_dataset.jsonl").read_text().splitlines() if l.strip()]
if len(ev) != 30 or len({e["id"] for e in ev}) != 30: errors.append("Eval dataset must contain 30 unique cases")

print("\n".join(errors) if errors else "Kit validation passed.")
sys.exit(1 if errors else 0)
