#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
مزامنة أسعار المورد من API Gozibra إلى users.db.

التشغيل من جذر المشروع:
    python scripts/sync_provider_prices.py
    python scripts/sync_provider_prices.py --active-only
    python scripts/sync_provider_prices.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _configure_stdout() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _load_env() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        print(f"ملف .env غير موجود: {env_file}", file=sys.stderr)
        sys.exit(1)
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=True)
    except ImportError:
        import os

        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


async def _run(active_only: bool) -> int:
    from database import init_db
    from services.provider_price_sync import refresh_provider_prices

    init_db()
    result = await refresh_provider_prices(active_only=active_only)

    print()
    print("=== مزامنة أسعار المورد ===")
    print(f"  حسابات API المجلوبة: {result.fetched_accounts}")
    print(f"  إدخالات الكتالوج:     {result.catalog_entries}")
    print(f"  صفوف مفحوصة:         {result.rows_scanned}")
    print(f"  محدّثة:              {result.updated}")
    print(f"  بدون تغيير:          {result.unchanged}")
    print(f"  غير موجودة في API:   {result.missing_in_api}")
    print(f"  تخطي (تنفيذ يدوي):   {result.skipped_admin}")
    if result.errors:
        print("  أخطاء:")
        for err in result.errors:
            print(f"    - {err}")
    print()

    if not result.success:
        return 1
    return 0


def main() -> int:
    _configure_stdout()
    _load_env()

    parser = argparse.ArgumentParser(description="مزامنة أسعار المورد إلى smm_services")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--active-only",
        action="store_true",
        help="الخدمات النشطة في الكتالوج فقط (افتراضي)",
    )
    scope.add_argument(
        "--all",
        action="store_true",
        help="كل الصفوف في smm_services بما فيها غير النشطة",
    )
    args = parser.parse_args()
    active_only = not args.all
    try:
        return asyncio.run(_run(active_only=active_only))
    except KeyboardInterrupt:
        print("\nتم الإيقاف.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
