# -*- coding: utf-8 -*-
"""
تحميل كتالوج الخدمات من services.json مع كتابة ذرّية وقراءة آمنة أثناء التحديث.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent
SERVICES_JSON_PATH = Path(
    os.environ.get("SOLDIUM_SERVICES_JSON", str(_DIR / "services.json")),
).resolve()

_READ_RETRIES = 5
_READ_RETRY_DELAY = 0.06


def _read_json_file(path: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(_READ_RETRIES):
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                raise json.JSONDecodeError("empty file", raw, 0)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("services.json must be a JSON object")
            return data
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < _READ_RETRIES:
                time.sleep(_READ_RETRY_DELAY)
    assert last_error is not None
    raise last_error


def _bootstrap_from_embedded() -> dict[str, Any]:
    from services_config_embedded import SERVICES

    data = copy.deepcopy(SERVICES)
    save_services_dict(data)
    logger.info("Bootstrapped %s from embedded catalog", SERVICES_JSON_PATH)
    return data


def load_services_dict(*, allow_bootstrap: bool = True) -> dict[str, Any]:
    path = SERVICES_JSON_PATH
    if path.is_file():
        try:
            return _read_json_file(path)
        except Exception as exc:
            logger.error("Failed to read %s: %s", path, exc)
            if allow_bootstrap:
                logger.warning("Falling back to embedded catalog")
                return _bootstrap_from_embedded()
            raise
    if allow_bootstrap:
        return _bootstrap_from_embedded()
    return {}


def save_services_dict(data: dict[str, Any]) -> None:
    path = SERVICES_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def reload_services_mapping(target: dict[str, Any]) -> None:
    """تحديث القاموس المشترك في الذاكرة دون استبدال المرجع (متوافق مع import SERVICES)."""
    fresh = load_services_dict()
    target.clear()
    target.update(fresh)
