"""Agent Invoker Lambda (Section 5.3, OPS-005).

Runtime entry point that sits between EventBridge and the orchestrator control loop
(``agents.orchestrator.handle_event``). Everything in this package only ever receives
already-normalized EventBridge events -- the 8 rules documented in ``resource.md``.

Raw IoT telemetry and inbound-channel (SNS/SQS RCS/SMS/WhatsApp) payloads are *not*
handled here; they are normalized into one of the 8 EventBridge shapes upstream (the
IoT topic rule's own Lambda action, and ``inbound.normalizer`` respectively) before
reaching the Invoker. Keeping that boundary sharp avoids duplicating the Inbound
Normalizer's job inside this Lambda.
"""
