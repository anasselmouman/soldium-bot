# -*- coding: utf-8 -*-
"""Catalog Telegram callback_data must stay within Telegram's 64-byte limit."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from keyboards.orders import (
    TELEGRAM_CALLBACK_DATA_MAX_BYTES,
    _find_catalog_node,
    _find_catalog_parent_entry_id,
    _section_nav_callback,
    _subsection_nav_callback,
    build_catalog_node_menu,
    build_platforms_menu,
    build_sections_menu,
    build_services_menu,
    build_subsections_menu,
    catalog_node_back_callback,
)


def _callback_data_values(markup) -> list[str]:
    values: list[str] = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                values.append(str(btn.callback_data))
    return values


def _assert_all_callbacks_within_limit(markup, *, context: str) -> None:
    for cb in _callback_data_values(markup):
        size = len(cb.encode("utf-8"))
        assert size <= TELEGRAM_CALLBACK_DATA_MAX_BYTES, (
            f"{context}: callback {cb!r} is {size} bytes (max {TELEGRAM_CALLBACK_DATA_MAX_BYTES})"
        )


def _sample_catalog_tree() -> dict[str, Any]:
    """Minimal Catalog-shaped tree with long ent_* ids (production-like)."""
    root = "ent_f9316ac1eec84918a80ef23dc37f9d91"
    sec = "ent_8c13fe6b62584f86b3972af4cc37207d"
    sub = "ent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    deep = "ent_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    svc = "svc_cccccccccccccccccccccccccccccccc"
    return {
        root: {
            "title": "خدمات فيسبوك",
            "entry_id": root,
            "sections": {
                sec: {
                    "title": "المتابعين",
                    "entry_id": sec,
                    "sections": {
                        sub: {
                            "title": "فرعي",
                            "entry_id": sub,
                            "sections": {
                                deep: {
                                    "title": "أعمق",
                                    "entry_id": deep,
                                    "sections": {},
                                    "items": [
                                        {
                                            "id": svc,
                                            "name": "خدمة",
                                            "price": 1.0,
                                        }
                                    ],
                                    "direct_items": [],
                                    "subsections": {},
                                }
                            },
                            "items": [],
                            "direct_items": [],
                            "subsections": {},
                        }
                    },
                    "items": [{"id": "svc_direct_sec", "name": "مباشر", "price": 2.0}],
                    "direct_items": [],
                    "subsections": {},
                }
            },
            "direct_items": [],
            "items": [],
            "subsections": {},
        }
    }


@pytest.fixture
def catalog_tree(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    tree = _sample_catalog_tree()

    class _SF:
        backend_name = "catalog"

        def refresh(self) -> None:
            return None

    monkeypatch.setattr("keyboards.orders._services", lambda: tree)
    monkeypatch.setattr(
        "keyboards.orders._is_catalog_tree", lambda: True
    )
    # handlers import navigation via storefront helper used by keyboards
    monkeypatch.setattr(
        "storefront.get_storefront", lambda force_reload=False: _SF()
    )
    return tree


def test_catalog_section_callback_uses_single_node_id(catalog_tree: dict) -> None:
    root = next(iter(catalog_tree))
    sec = next(iter(catalog_tree[root]["sections"]))
    cb = _section_nav_callback(root, sec)
    assert cb == f"order:node:{sec}"
    assert len(cb.encode()) <= TELEGRAM_CALLBACK_DATA_MAX_BYTES
    # Old dual-id format would exceed the limit
    legacy_style = f"order:section:{root}:{sec}"
    assert len(legacy_style.encode()) > TELEGRAM_CALLBACK_DATA_MAX_BYTES


def test_catalog_subsection_callback_uses_single_node_id(catalog_tree: dict) -> None:
    root = next(iter(catalog_tree))
    sec = next(iter(catalog_tree[root]["sections"]))
    sub = next(iter(catalog_tree[root]["sections"][sec]["sections"]))
    cb = _subsection_nav_callback(root, sec, sub)
    assert cb == f"order:node:{sub}"
    assert len(cb.encode()) <= TELEGRAM_CALLBACK_DATA_MAX_BYTES
    legacy_style = f"o:ss:{root}:{sec}:{sub}"
    assert len(legacy_style.encode()) > TELEGRAM_CALLBACK_DATA_MAX_BYTES


def test_catalog_platform_sections_and_node_menus_within_limit(
    catalog_tree: dict,
) -> None:
    root = next(iter(catalog_tree))
    sec = next(iter(catalog_tree[root]["sections"]))
    sub = next(iter(catalog_tree[root]["sections"][sec]["sections"]))

    _assert_all_callbacks_within_limit(build_platforms_menu(), context="platforms")
    sections_markup = build_sections_menu(root)
    _assert_all_callbacks_within_limit(sections_markup, context="sections")
    assert any(cb.startswith("order:node:") for cb in _callback_data_values(sections_markup))
    assert not any(
        cb.startswith("order:section:") and cb.count(":") >= 3
        for cb in _callback_data_values(sections_markup)
    )

    node_markup = build_catalog_node_menu(
        sec, back_callback=catalog_node_back_callback(sec, platform_key=root)
    )
    _assert_all_callbacks_within_limit(node_markup, context="catalog_node")
    assert f"order:node:{sub}" in _callback_data_values(node_markup)

    deep_markup = build_catalog_node_menu(
        sub, back_callback=catalog_node_back_callback(sub, platform_key=root)
    )
    _assert_all_callbacks_within_limit(deep_markup, context="deep_node")


def test_catalog_node_resolves_correct_entry(catalog_tree: dict) -> None:
    root = next(iter(catalog_tree))
    sec = next(iter(catalog_tree[root]["sections"]))
    node = _find_catalog_node(sec)
    assert node is not None
    assert node.get("title") == "المتابعين"
    assert _find_catalog_parent_entry_id(sec) == root
    assert catalog_node_back_callback(sec) == f"order:platform:{root}"


def test_catalog_callbacks_survive_fresh_tree_reload(catalog_tree: dict) -> None:
    """Resolution uses navigation tree keys (durable), not an in-memory token map."""
    root = next(iter(catalog_tree))
    sec = next(iter(catalog_tree[root]["sections"]))
    cb = _section_nav_callback(root, sec)
    entry_id = cb.split(":", maxsplit=2)[-1]
    # Simulate fresh process: same tree reload, no session map.
    reloaded = _sample_catalog_tree()
    with patch("keyboards.orders._services", return_value=reloaded):
        node = _find_catalog_node(entry_id)
    assert node is not None
    assert str(node.get("entry_id") or entry_id) == sec or entry_id == sec


def test_legacy_section_callbacks_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_tree = {
        "facebook": {
            "title": "فيسبوك",
            "sections": {
                "likes": {
                    "title": "لايكات",
                    "items": [{"id": "1", "name": "svc", "price": 1}],
                    "subsections": {
                        "premium": {
                            "title": "مميز",
                            "items": [{"id": "2", "name": "svc2", "price": 2}],
                        }
                    },
                }
            },
            "direct_items": [],
        }
    }
    monkeypatch.setattr("keyboards.orders._services", lambda: legacy_tree)
    monkeypatch.setattr("keyboards.orders._is_catalog_tree", lambda: False)

    assert _section_nav_callback("facebook", "likes") == "order:section:facebook:likes"
    assert (
        _subsection_nav_callback("facebook", "likes", "premium")
        == "o:ss:facebook:likes:premium"
    )
    markup = build_sections_menu("facebook")
    cbs = _callback_data_values(markup)
    assert "order:section:facebook:likes" in cbs
    assert not any(c.startswith("order:node:") for c in cbs)
    _assert_all_callbacks_within_limit(markup, context="legacy_sections")

    sub_markup = build_subsections_menu("facebook", "likes")
    assert "o:ss:facebook:likes:premium" in _callback_data_values(sub_markup)
    _assert_all_callbacks_within_limit(sub_markup, context="legacy_subsections")

    svc_markup = build_services_menu("facebook", "likes", "premium")
    assert any(
        c == "order:section:facebook:likes" for c in _callback_data_values(svc_markup)
    )
