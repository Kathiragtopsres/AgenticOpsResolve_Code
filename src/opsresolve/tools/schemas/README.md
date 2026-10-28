# Tool JSON Schemas

Per Section 6.3, tool contracts are defined as JSON Schema and registered with the AgentCore
Gateway. The canonical schema set is maintained at the repository root under `schemas/tools/`
(shared with contract tests). This folder holds any tool-adapter-local schema overrides or
generated artifacts.

Adapters validate inputs and outputs against `schemas/tools/<toolName>.schema.json` before
returning. See `tests/contract/` for the schema-vs-adapter contract tests.
