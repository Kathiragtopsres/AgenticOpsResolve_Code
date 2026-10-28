# Agent prompts

Versioned system prompts, one file per agent: `<agent>.v<n>.md` (Section 5.6). The prompt
version is recorded on every trace, so bump the version rather than editing in place once a
prompt is referenced by a deployed Runtime.

Prompt structure (Section 5.6):
1. Role and objective
2. Operating constraints (autonomy, safety, honesty about uncertainty)
3. Tool usage rules
4. Output schema (the Pydantic model in `../schemas/`)
5. Two worked examples
6. Refusal and escalation conditions

Rules:
- Customer text is always wrapped as untrusted data (`<customer_message>` tags) and never
  interpreted as instructions.
- Numbers shown to customers (times, costs) MUST come from tool outputs; copy, do not compute.
