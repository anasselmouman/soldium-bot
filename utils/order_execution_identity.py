# -*- coding: utf-8 -*-
"""Phase 8G — order execution identity helpers (snapshot / Gen model / wire encode)."""

from __future__ import annotations


class InvalidProviderExternalServiceId(ValueError):
    """External provider service id is not acceptable for the wire boundary."""


def is_seed_demo_service_id(service_id: object) -> bool:
    """Historical analytics seed rows must never be late-fulfilled."""
    text = str(service_id or "").strip()
    return text.startswith("seed_") or text.startswith("extra_")


def order_external_snapshot(order: dict) -> str:
    return str(order.get("external_service_id_snapshot") or "").strip()


def is_gen1_execution_order(order: dict) -> bool:
    return bool(order_external_snapshot(order))


def encode_provider_external_service_id_for_wire(external_service_id: object) -> str:
    """Validate opaque TEXT for current numeric Provider form APIs.

    Retains the digit string (including leading zeros) for the HTTP form field.
    Never maps invalid input to ``0``.
    """
    text = str(external_service_id or "").strip()
    if not text or not text.isdigit() or int(text) == 0:
        raise InvalidProviderExternalServiceId(
            f"معرّف خدمة المزوّد غير صالح: {external_service_id!r}"
        )
    return text
