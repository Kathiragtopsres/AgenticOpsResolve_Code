"""Mock WFM / FSM (Section 6.4).

Seeded: technicians with skills, certifications, shifts and a live-position simulation, plus
work orders. Includes fault flags used by scenarios (e.g. expired cert, missing skill,
out-of-region, job-overrun) driving the recovery journeys. Backs findQualifiedTechnicians,
getTechnicianStatus, proposeAppointmentSlots, createServiceAppointment, reassignTechnician,
updateWorkOrder.
Production adapters: ServiceNow FSM, Salesforce Field Service, IFS, Oracle Field Service.
"""
from __future__ import annotations


def list_technicians(region: str | None = None) -> list[dict]:  # pragma: no cover
    raise NotImplementedError


def get_technician_status(technician_id: str) -> dict:  # pragma: no cover
    raise NotImplementedError


def create_work_order(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def update_work_order(work_order_id: str, patch: dict) -> dict:  # pragma: no cover
    raise NotImplementedError
