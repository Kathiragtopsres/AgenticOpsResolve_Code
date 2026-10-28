"""Scheduling domain tools (Section 6.2, Listing 6-2).

  proposeAppointmentSlots  (Read, Slot engine over WFM calendars) — engines.slot_engine
  createServiceAppointment (Write, Mock FSM, idempotent) — requires consentRef (Section 10.2)
  reassignTechnician       (Write, Mock FSM, idempotent)
  updateWorkOrder          (Write, Mock FSM, idempotent)
"""
from __future__ import annotations


def propose_appointment_slots(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def create_service_appointment(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def reassign_technician(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def update_work_order(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError
