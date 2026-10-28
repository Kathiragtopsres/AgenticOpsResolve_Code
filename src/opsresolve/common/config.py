"""Runtime configuration and resource naming.

Values are resolved from environment variables and SSM parameters at runtime.
Locally (tests, simulators) they fall back to sensible defaults so the code is
importable without an AWS account.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


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

    # Tenant/scenario config; item keys are documented in common/tenants.py.
    @property
    def tenants_table(self) -> str:
        return f"{self.prefix}-tenants"

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


# --- Demo case-lifecycle settings (tasks.md "NEXT SESSION 3") ---------------------------------------
# Read from the ``lifecycle:`` block of config/demo.yaml. Where the file lives depends on the
# packaging: <repo>/config (local) or <zip root>/config (Lambda /var/task/config, AgentCore runtime).
# Defaults equal the demo values, so a missing file or PyYAML changes nothing.
_DEMO_CONFIG_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "config" / "demo.yaml",
    Path(__file__).resolve().parents[2] / "config" / "demo.yaml",
)


@dataclass(frozen=True)
class LifecycleSettings:
    # Demo only: what a remote fix does once its wait elapses. FAIL -> field visit needed; PASS -> resolved.
    remote_fix_outcome: str = "FAIL"
    remote_fix_wait_minutes: float = 2
    # VERIFYING closes when this window has elapsed AND a CSAT score is in...
    verification_window_minutes: float = 15
    # ...or, with no CSAT by then, anyway (no score stored).
    csat_wait_minutes: float = 60
    # AWAITING_CUSTOMER: silence for this long re-runs the diagnosis (never escalates).
    customer_reply_wait_hours: float = 24


@lru_cache(maxsize=1)
def lifecycle() -> LifecycleSettings:
    """Lifecycle settings from config/demo.yaml (``lifecycle:`` block); defaults when absent or unreadable."""
    defaults = LifecycleSettings()
    path = next((p for p in _DEMO_CONFIG_CANDIDATES if p.exists()), None)
    if path is None:
        return defaults
    try:
        import yaml

        block = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("lifecycle") or {}
        outcome = str(block.get("remote_fix_outcome", defaults.remote_fix_outcome)).upper()
        return LifecycleSettings(
            remote_fix_outcome=outcome if outcome in ("FAIL", "PASS") else defaults.remote_fix_outcome,
            remote_fix_wait_minutes=float(block.get("remote_fix_wait_minutes", defaults.remote_fix_wait_minutes)),
            verification_window_minutes=float(
                block.get("verification_window_minutes", defaults.verification_window_minutes)),
            csat_wait_minutes=float(block.get("csat_wait_minutes", defaults.csat_wait_minutes)),
            customer_reply_wait_hours=float(
                block.get("customer_reply_wait_hours", defaults.customer_reply_wait_hours)),
        )
    except Exception as exc:  # a bad config file must not stop case handling
        from .logging import get_logger

        get_logger(__name__).error(f"Could not read lifecycle settings from {path}: {exc}; using defaults")
        return defaults
