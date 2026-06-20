# -*- coding: utf-8 -*-
"""مزامنة ترتيب وعناوين تيليجرام في services.json وقاعدة البيانات."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services_catalog_db import (
    PLATFORM_SECTION_ORDER,
    PLATFORM_SECTION_TITLES,
    PLATFORM_SUBSECTION_TITLES,
)

def _reorder_sections(data: dict) -> None:
    order = PLATFORM_SECTION_ORDER["telegram"]
    sections = data["telegram"]["sections"]
    data["telegram"]["sections"] = {
        k: sections[k] for k in order if k in sections
    }
    for k, v in sections.items():
        if k not in data["telegram"]["sections"]:
            data["telegram"]["sections"][k] = v


def _apply_titles(data: dict) -> None:
    for section_key, title in PLATFORM_SECTION_TITLES["telegram"].items():
        section = data["telegram"]["sections"].get(section_key)
        if section:
            section["title"] = title
    for (section_key, subsection_key), title in PLATFORM_SUBSECTION_TITLES[
        "telegram"
    ].items():
        section = data["telegram"]["sections"].get(section_key) or {}
        subsection = (section.get("subsections") or {}).get(subsection_key)
        if subsection:
            subsection["title"] = title


def sync_services_json() -> None:
    path = ROOT / "services.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    _reorder_sections(data)
    _apply_titles(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_db() -> None:
    db_path = ROOT / "users.db"
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as conn:
        for section_key, title in PLATFORM_SECTION_TITLES["telegram"].items():
            conn.execute(
                """
                UPDATE smm_services
                SET section_title = ?
                WHERE platform_key = 'telegram' AND section_key = ?
                """,
                (title, section_key),
            )
        for (section_key, subsection_key), title in PLATFORM_SUBSECTION_TITLES[
            "telegram"
        ].items():
            conn.execute(
                """
                UPDATE smm_services
                SET subsection_title = ?
                WHERE platform_key = 'telegram'
                  AND section_key = ?
                  AND subsection_key = ?
                """,
                (title, section_key, subsection_key),
            )
        conn.commit()


if __name__ == "__main__":
    sync_services_json()
    sync_db()
    print("telegram catalog synced")
