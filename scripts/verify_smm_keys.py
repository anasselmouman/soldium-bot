#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
فحص حيّ لمفاتيح SMM الأربعة في .env — يتحقق من الاتصال برصيد Gozibra لكل حساب.

التشغيل من جذر المشروع:
    python scripts/verify_smm_keys.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_API_URL = "https://gozibra.com/api/v2"

ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("instagram", "Instagram", "SMM_KEY_INSTAGRAM"),
    ("facebook", "Facebook", "SMM_KEY_FACEBOOK"),
    ("tiktok", "TikTok", "SMM_KEY_TIKTOK"),
    ("default", "Default", "SMM_KEY_DEFAULT"),
)


class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    WHITE = "\033[97m"


@dataclass(frozen=True)
class KeyCheckResult:
    account_type: str
    account_label: str
    env_var: str
    success: bool
    balance_usd: str | None
    currency: str | None
    error: str | None
    key_hint: str


def _configure_stdout() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0007)
    except Exception:
        pass


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{_C.RESET}"


def load_env_strict() -> None:
    """تحميل المتغيرات من .env فقط (مع استبدال القيم الحالية)."""
    if not ENV_FILE.is_file():
        print(_color(f"✗ ملف .env غير موجود: {ENV_FILE}", _C.RED))
        sys.exit(1)

    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=True)
    except ImportError:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _mask_key(api_key: str) -> str:
    key = api_key.strip()
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}…{key[-4:]}"


def _parse_balance_payload(data: object) -> tuple[str, str]:
    if not isinstance(data, dict):
        raise ValueError("استجابة الرصيد ليست كائناً JSON متوقعاً")

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    balance_raw = data.get("balance")
    if balance_raw is None:
        raise ValueError("حقل balance غير موجود في الاستجابة")

    currency = str(data.get("currency") or "USD").strip().upper() or "USD"
    try:
        balance_value = float(str(balance_raw).replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"قيمة balance غير رقمية: {balance_raw!r}") from exc

    return f"{balance_value:.4f}", currency


async def _fetch_balance(
    session: aiohttp.ClientSession,
    *,
    api_url: str,
    api_key: str,
) -> tuple[str, str]:
    payload = {"key": api_key, "action": "balance"}
    async with session.post(api_url, data=payload) as response:
        raw = await response.read()
        data: object | None = None
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None

        if response.status == 401:
            err = "Invalid API key"
            if isinstance(data, dict) and data.get("error"):
                err = str(data["error"])
            raise RuntimeError(err)

        if response.status >= 400:
            text = raw.decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"HTTP {response.status}: {text or 'no body'}")

        if data is None:
            raise ValueError(f"استجابة فارغة أو JSON غير صالح (HTTP {response.status})")

        return _parse_balance_payload(data)


async def check_account(
    session: aiohttp.ClientSession,
    *,
    account_type: str,
    account_label: str,
    env_var: str,
    api_url: str,
    api_key: str | None,
) -> KeyCheckResult:
    if not api_key or not str(api_key).strip():
        return KeyCheckResult(
            account_type=account_type,
            account_label=account_label,
            env_var=env_var,
            success=False,
            balance_usd=None,
            currency=None,
            error=f"المتغير {env_var} غير معرّف أو فارغ في .env",
            key_hint="—",
        )

    key = str(api_key).strip()
    try:
        balance, currency = await _fetch_balance(session, api_url=api_url, api_key=key)
        return KeyCheckResult(
            account_type=account_type,
            account_label=account_label,
            env_var=env_var,
            success=True,
            balance_usd=balance,
            currency=currency,
            error=None,
            key_hint=_mask_key(key),
        )
    except Exception as exc:
        return KeyCheckResult(
            account_type=account_type,
            account_label=account_label,
            env_var=env_var,
            success=False,
            balance_usd=None,
            currency=None,
            error=str(exc).strip() or type(exc).__name__,
            key_hint=_mask_key(key),
        )


async def run_checks(api_url: str, keys: dict[str, str | None]) -> list[KeyCheckResult]:
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            check_account(
                session,
                account_type=account_type,
                account_label=label,
                env_var=env_var,
                api_url=api_url,
                api_key=keys.get(env_var),
            )
            for account_type, label, env_var in ACCOUNTS
        ]
        return list(await asyncio.gather(*tasks))


def _print_header(api_url: str) -> None:
    line = "=" * 72
    print()
    print(_color(line, _C.CYAN))
    print(_color("  SOLDIUM — Live SMM API Health Check (Gozibra)", _C.BOLD + _C.CYAN))
    print(_color(f"  .env → {ENV_FILE}", _C.DIM))
    print(_color(f"  API URL → {api_url}", _C.DIM))
    print(_color(line, _C.CYAN))
    print()


def _print_results(results: list[KeyCheckResult]) -> int:
    col_account = 12
    col_status = 10
    col_balance = 18
    col_key = 14

    header = (
        f"{'Account':<{col_account}}  "
        f"{'Status':<{col_status}}  "
        f"{'Balance (USD)':<{col_balance}}  "
        f"{'Key':<{col_key}}  "
        f"Details"
    )
    print(_color(header, _C.BOLD + _C.WHITE))
    print(_color("-" * 72, _C.DIM))

    failures = 0
    for row in results:
        if row.success:
            status_txt = _color("Success", _C.GREEN)
            balance_txt = _color(
                f"{row.balance_usd} {row.currency or 'USD'}",
                _C.GREEN,
            )
            details = _color("OK", _C.DIM)
        else:
            failures += 1
            status_txt = _color("Failed", _C.RED)
            balance_txt = _color("—", _C.DIM)
            details = _color(row.error or "Unknown error", _C.RED)

        print(
            f"{row.account_label:<{col_account}}  "
            f"{status_txt:<{col_status + 9}}  "
            f"{balance_txt:<{col_balance + 9}}  "
            f"{row.key_hint:<{col_key}}  "
            f"{details}"
        )

    print()
    ok_count = len(results) - failures
    summary = f"  Result: {ok_count}/{len(results)} accounts healthy"
    if failures:
        print(_color(summary, _C.YELLOW))
        print(_color("  ✗ One or more keys failed — fix .env before production.", _C.RED))
    else:
        print(_color(summary, _C.GREEN))
        print(_color("  ✓ All SMM keys are valid and reachable.", _C.GREEN))
    print()
    return failures


def main() -> int:
    _configure_stdout()
    load_env_strict()

    api_url = os.environ.get("API_URL", DEFAULT_API_URL).strip() or DEFAULT_API_URL
    keys = {env_var: os.environ.get(env_var) for _, _, env_var in ACCOUNTS}

    _print_header(api_url)

    try:
        results = asyncio.run(run_checks(api_url, keys))
    except KeyboardInterrupt:
        print(_color("\n  Interrupted.", _C.YELLOW))
        return 130

    failures = _print_results(results)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
