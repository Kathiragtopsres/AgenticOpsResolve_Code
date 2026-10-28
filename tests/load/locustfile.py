"""NFR-04 load profile: 50 concurrent cases, 10 inbound messages/s for 10 minutes.
Targets the test-only inbound ingestion endpoint (API Gateway -> SNS publish) exposed in the dev/demo stack
and the WhatsApp Flow endpoint. Run: locust -f locustfile.py --host https://<api-id>.execute-api.<region>.amazonaws.com/demo
"""
import json, random, uuid
from locust import HttpUser, task, between, constant_throughput

class InboundUser(HttpUser):
    wait_time = constant_throughput(0.2)   # 50 users x 0.2 = 10 msg/s
    def on_start(self):
        self.case = f"SR-LOAD-{uuid.uuid4().hex[:6]}"
        self.phone = f"+1202555{random.randint(1000,9999)}"
    @task(4)
    def free_text(self):
        self.client.post("/test/inbound", json={"channel": "RCS", "from": self.phone, "text": "Any update on my visit?", "caseId": self.case})
    @task(1)
    def flow_slots(self):
        with self.client.post("/whatsapp/flow", json={"version": "3.0", "action": "data_exchange", "screen": "PICK_SLOT",
                              "flow_token": f"flow-{self.case}", "data": {"date": "2026-10-21"}}, catch_response=True) as r:
            if r.elapsed.total_seconds() > 1.5:
                r.failure("Flow endpoint > 1.5 s")
