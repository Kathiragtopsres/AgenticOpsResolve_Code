"""Mock CRM / Contracts (Section 6.4).

Seeded: customers, sites, contacts, and SLA tiers (GOLD_4H / SILVER_8H / BRONZE_NBD).
Backs getAssetContext (customer/site/contact) and checkServiceEntitlement (coverage).
Production adapters: Salesforce Service Cloud, Dynamics 365, SAP Service.
"""
from __future__ import annotations


def get_customer(customer_id: str) -> dict:  # pragma: no cover
    raise NotImplementedError


def get_contract(customer_id: str, asset_id: str) -> dict:  # pragma: no cover
    raise NotImplementedError
