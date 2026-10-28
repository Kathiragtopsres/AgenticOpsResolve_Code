# Message quality rubric (LLM-as-judge, 1-5)

| Score | Clarity | Accuracy | Brevity | Next step |
|---|---|---|---|---|
| 5 | Plain language, no jargon | Every time/cost/name matches tool output | RCS text < 300 chars; 3 options | Clear action or human path |
| 3 | Minor jargon | Correct but vague | Slightly long | Next step implied |
| 1 | Confusing | Any invented time, cost or promise | Wall of text | Dead end |

Automatic fail: any number not present in tool outputs; internal IDs other than the case reference; missing opt-out/human path on alerts.
