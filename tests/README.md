# tests/ — Testing and quality (Section 16)

| Folder | Level | Tooling |
|---|---|---|
| `unit/` | Engines, router, postback, normaliser parsers | pytest, hypothesis |
| `contract/` | Production vs. reference oracles; tool schema vs. adapter; CDS payload validators | pytest + jsonschema |
| `eval/` | 30-scenario agent evaluation set (agent behaviour) | AgentCore Evaluations + local harness |
| `e2e/` | Journeys A–E via the Scenario Controller | pytest e2e + `scripts/rehearse.py` |
| `load/` | NFR-04 load profile | Locust |
| `security/` | Injection prompts, forged postbacks, non-approver replies, log PII scan | custom suite |

Policy (Cedar) allow/deny fixtures live with `policies/fixtures/` and run in CI.

Run everything locally without AWS:

```bash
pip install -e ".[dev]"
pytest -q
python scripts/validate_kit.py
```
