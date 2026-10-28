# Contract tests (Section 16.1)

Assert that production components in `src/opsresolve/` behave identically to the reference
oracles in `reference/opsresolve_ref/`, and that tool adapters honour the JSON Schemas in
`schemas/tools/` (RCS limits, WhatsApp template params, error envelope shape).

Run: `pytest tests/contract`
