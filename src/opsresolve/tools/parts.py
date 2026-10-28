"""Parts domain tools (Section 6.2, Listing 6-1).

  findAndReservePart      (Write, Mock ERP inventory, idempotent)
  releasePartReservation  (Write, Mock ERP, idempotent)
  transferPart            (Write, Mock ERP, idempotent)

HOLD/CONFIRM modes; above-threshold value returns NEEDS_APPROVAL. The adapter re-validates
the value threshold and coverage as defence in depth (Section 10.2).
"""
from __future__ import annotations


def find_and_reserve_part(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def release_part_reservation(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def transfer_part(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError
