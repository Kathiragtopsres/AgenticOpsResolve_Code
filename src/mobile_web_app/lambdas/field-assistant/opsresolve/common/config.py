"""Runtime configuration and resource naming.

Values are resolved from environment variables and SSM parameters at runtime.
Locally (tests, simulators) they fall back to sensible defaults so the code is
importable without an AWS account.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("OPSRESOLVE_ENV", "dev")
    region: str = os.getenv("AWS_REGION", "us-east-1")

    @property
    def prefix(self) -> str:
        return f"opsresolve-{self.env}"

    # DynamoDB tables (Section 9.1) — case store, created via CLI (see resource.md)
    @property
    def cases_table(self) -> str:
        return f"{self.prefix}-cases"

    @property
    def conversations_table(self) -> str:
        return f"{self.prefix}-conversations"

    # Lakshmi's requestApproval table (hash key approvalId only, no GSI) -- the approvals
    # source of truth; the APR# path in the cases table is retired.
    @property
    def approvals_table(self) -> str:
        return f"{self.prefix}-approvals"

    # Deployed EventBridge bus. NOTE common/events.py BUS_NAME ("opsresolve-bus") is wrong.
    @property
    def bus_name(self) -> str:
        return f"{self.prefix}-bus"

    @property
    def idempotency_table(self) -> str:
        return f"{self.prefix}-idempotency"

    # Mock enterprise data tables (OPS-003, Subha) — CRM/EAM/ERP/FSM seed data.
    # pk = "<ENTITY>#<id>" / sk = "META" for all except mock_inventory
    # (pk = "INV#<depotId>#<partId>").
    @property
    def mock_customers_table(self) -> str:
        return f"{self.prefix}-mock-customers"

    @property
    def mock_sites_table(self) -> str:
        return f"{self.prefix}-mock-sites"

    @property
    def mock_contacts_table(self) -> str:
        return f"{self.prefix}-mock-contacts"

    @property
    def mock_assets_table(self) -> str:
        return f"{self.prefix}-mock-assets"

    @property
    def mock_contracts_table(self) -> str:
        return f"{self.prefix}-mock-contracts"

    @property
    def mock_depots_table(self) -> str:
        return f"{self.prefix}-mock-depots"

    @property
    def mock_parts_table(self) -> str:
        return f"{self.prefix}-mock-parts"

    @property
    def mock_inventory_table(self) -> str:
        return f"{self.prefix}-mock-inventory"

    @property
    def mock_technicians_table(self) -> str:
        return f"{self.prefix}-mock-technicians"

    @property
    def mock_work_order_history_table(self) -> str:
        return f"{self.prefix}-mock-work-order-history"

    @property
    def mock_approvers_table(self) -> str:
        return f"{self.prefix}-mock-approvers"

    @property
    def mock_couriers_table(self) -> str:
        return f"{self.prefix}-mock-couriers"

    @property
    def mock_network_table(self) -> str:
        return f"{self.prefix}-mock-network"

    # Telemetry latest-state table (OPS-003/OPS-004, Subha).
    # pk = "TELEMETRY#<assetId>#<profile>" / sk = ts (ISO-8601 string).
    @property
    def telemetry_table(self) -> str:
        return f"{self.prefix}-telemetry"

    # S3 buckets (Section 9.1)
    @property
    def knowledge_bucket(self) -> str:
        return f"{self.prefix}-knowledge"

    @property
    def artifacts_bucket(self) -> str:
        return f"{self.prefix}-artifacts"

    # SSM parameter paths (Section 5.1)
    @property
    def model_param_prefix(self) -> str:
        return f"/{self.prefix}/models"

    @property
    def demo_clock_param(self) -> str:
        return f"/{self.prefix}/demo/clockFactor"


settings = Settings()
