# -*- coding: utf-8 -*-
"""Telegram Storefront facade — Phase 9Q + controlled Catalog pilot.

Handlers/keyboards must use ``get_storefront()`` rather than selecting
backends themselves. Default remains Legacy.

Pilot (STOREFRONT_CATALOG_PILOT=enabled) scopes Catalog to the published
cohort only; STOREFRONT_BACKEND must stay legacy for that mode.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

_BOT_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _BOT_ROOT.parent / "soldium-dashboard"
if _DASHBOARD_ROOT.is_dir() and str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from catalog_core.storefront_gateway import (  # noqa: E402
    CatalogStorefrontBackend,
    LegacyStorefrontBackend,
    StorefrontBackend,
    build_storefront,
    clear_storefront_cache,
    order_intent_to_create_bridge,
    reinitialize_storefront_selection,
    resolve_storefront_backend_name,
    set_storefront_backend_override,
    shadow_pair,
)
from catalog_core.storefront_pilot import (  # noqa: E402
    PilotHybridStorefrontBackend,
    resolve_catalog_pilot_enabled,
)

__all__ = [
    "CatalogStorefrontBackend",
    "LegacyStorefrontBackend",
    "PilotHybridStorefrontBackend",
    "StorefrontBackend",
    "clear_storefront_cache",
    "get_storefront",
    "navigation_tree",
    "order_intent_to_create_bridge",
    "reinitialize_storefront_selection",
    "resolve_storefront_backend_name",
    "set_storefront_backend_override",
    "shadow_pair",
]

_cached: StorefrontBackend | None = None


def _db_path() -> Path:
    try:
        from database import DB_PATH

        return Path(DB_PATH)
    except Exception:
        return _BOT_ROOT / "users.db"


def _open_catalog_connection() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def get_storefront(*, force_reload: bool = False) -> StorefrontBackend:
    """Central storefront accessor for Telegram. Default backend: legacy."""
    global _cached
    if _cached is not None and not force_reload:
        return _cached

    import config as bot_config
    from services_config import SERVICES, reload_services

    name = resolve_storefront_backend_name(
        getattr(bot_config, "STOREFRONT_BACKEND", None)
    )
    pilot_raw = getattr(bot_config, "STOREFRONT_CATALOG_PILOT", None)
    pilot_on = resolve_catalog_pilot_enabled(pilot_raw)

    if name == "catalog":
        # Full Catalog-only (not the 43-service production pilot).
        conn = _open_catalog_connection()
        _cached = build_storefront(backend="catalog", connection=conn)
    elif pilot_on:
        conn = _open_catalog_connection()
        _cached = build_storefront(
            backend="pilot",
            connection=conn,
            legacy_tree_loader=lambda: SERVICES,
            legacy_refresher=reload_services,
        )
    else:
        _cached = build_storefront(
            backend="legacy",
            legacy_tree_loader=lambda: SERVICES,
            legacy_refresher=reload_services,
        )
    return _cached


def navigation_tree() -> dict[str, Any]:
    """Nested tree for existing Telegram keyboards (Legacy shape)."""
    sf = get_storefront()
    if hasattr(sf, "navigation_tree"):
        return sf.navigation_tree()  # type: ignore[no-any-return]
    if isinstance(sf, LegacyStorefrontBackend):
        return sf.navigation_tree()
    if isinstance(sf, CatalogStorefrontBackend):
        return sf.navigation_tree()
    if isinstance(sf, PilotHybridStorefrontBackend):
        return sf.navigation_tree()
    return {}
