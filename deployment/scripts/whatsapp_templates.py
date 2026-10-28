"""Submit the OpsResolve WhatsApp message templates to Meta (via AWS End User Messaging Social) and show status.

Usage (from tcs-agentic-opsresolve\\):
    python scripts/whatsapp_templates.py            # list status only
    python scripts/whatsapp_templates.py --submit   # create the templates that don't exist yet, then list

Templates are UTILITY, English, short. Brand text is "Agentic OpsResolve" (the WABA display name is set on Meta's side).
Quick-reply button payloads are filled in at send time by the Gateway Lambda (signed postbacks), in button order.
Names/params must stay in sync with build_wa_template / notify_technician in gateway tools/lambda_handler.py.
"""
from __future__ import annotations

import json
import sys

import boto3

WABA_ID = "waba-4fba8293f662494ca0a1335bce91f6a6"
LANGUAGE = "en"
REGION = "us-east-1"


def _tpl(name: str, body: str, examples: list[str], buttons: list[str] | None = None) -> dict:
    components: list[dict] = [{"type": "BODY", "text": body, "example": {"body_text": [examples]}}]
    if buttons:
        components.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": b} for b in buttons]})
    return {"name": name, "language": LANGUAGE, "category": "UTILITY", "components": components}


TEMPLATES = [
    # Replaces opsresolve_case_update, which Meta re-categorised to MARKETING (vague body: "there is an update ...";
    # Meta delivery error 130472 for numbers in its marketing experiment). Meta's utility rule: non-promotional AND
    # specific to the user's own request, so the body names the request and states what changed ({{2}}).
    _tpl("opsresolve_case_progress",
         "Agentic OpsResolve: your service request {{1}} has moved to the next step: {{2}}. Tap below to continue.",
         ["SR-7D75DF8B44", "please choose a time for the service visit"], ["View update"]),
    _tpl("opsresolve_visit_confirmed",
         "Agentic OpsResolve: your service visit for request {{1}} is booked for {{2}}. Please confirm or reschedule.",
         ["SR-7D75DF8B44", "Mon 12 Oct 14:00-16:00 IST"], ["Confirm", "Reschedule"]),
    _tpl("opsresolve_technician_eta",
         "Agentic OpsResolve: the technician for request {{1}} is now expected at {{2}}. We will keep you updated.",
         ["SR-7D75DF8B44", "Mon 12 Oct 15:30 IST"]),
    _tpl("opsresolve_service_feedback",
         "Agentic OpsResolve: request {{1}} is complete. How was your service experience?",
         ["SR-7D75DF8B44"], ["Great", "Okay", "Poor"]),
    _tpl("opsresolve_job_assigned",
         "Agentic OpsResolve: new job {{1}} for asset {{2}} is assigned to you for {{3}}. Please confirm below.",
         ["SR-7D75DF8B44", "CHILLER-8821", "Mon 12 Oct 14:00-16:00 IST"], ["Accept", "Can't make it"]),
    _tpl("opsresolve_job_reassigned",
         "Agentic OpsResolve: job {{1}} has been reassigned to another technician. No action is needed.",
         ["SR-7D75DF8B44"]),
]


def existing(client) -> dict:
    out, kwargs = {}, {"id": WABA_ID}
    while True:
        resp = client.list_whatsapp_message_templates(**kwargs)
        for t in resp.get("templates", []):
            out[(t.get("templateName"), t.get("templateLanguage"))] = t
        if not resp.get("nextToken"):
            return out
        kwargs["nextToken"] = resp["nextToken"]


def main() -> None:
    client = boto3.client("socialmessaging", region_name=REGION)
    if "--submit" in sys.argv:
        have = existing(client)
        for tpl in TEMPLATES:
            if (tpl["name"], LANGUAGE) in have:
                print(f"skip   {tpl['name']} (exists)")
                continue
            resp = client.create_whatsapp_message_template(id=WABA_ID, templateDefinition=json.dumps(tpl).encode())
            print(f"create {tpl['name']}: status={resp.get('templateStatus')} metaTemplateId={resp.get('metaTemplateId')}")
    for (name, lang), t in sorted(existing(client).items()):
        print(f"{name:32} {lang:6} {t.get('templateStatus'):10} {t.get('templateCategory')} id={t.get('metaTemplateId')}")


if __name__ == "__main__":
    main()
