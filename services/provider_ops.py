# -*- coding: utf-8 -*-
"""عمليات تشغيل متعددة المزوّدين: فحص بدء التشغيل، أرصدة، صحة الاتصال."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from services.provider_registry import (
    ProviderBalanceSnapshot,
    fetch_all_provider_balances,
    list_active_provider_accounts,
    list_active_providers,
)

logger = logging.getLogger(__name__)


@dataclass
class StartupProviderCheckResult:
    providers: int = 0
    accounts: int = 0
    balances_ok: int = 0
    balances_failed: int = 0
    errors: list[str] = field(default_factory=list)
    snapshots: list[ProviderBalanceSnapshot] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.providers > 0 and self.accounts > 0 and self.balances_ok > 0


async def verify_providers_at_startup() -> StartupProviderCheckResult:
    """يتحقق من وجود مزوّدين نشطين ويجلب أرصدتهم."""
    result = StartupProviderCheckResult()
    providers = list_active_providers()
    accounts = list_active_provider_accounts()
    result.providers = len(providers)
    result.accounts = len(accounts)
    if not providers:
        result.errors.append("لا يوجد أي مزوّد نشط في جدول providers.")
        return result
    if not accounts:
        result.errors.append("لا توجد حسابات API نشطة في provider_accounts.")
        return result

    snapshots = await fetch_all_provider_balances()
    result.snapshots = snapshots
    for snap in snapshots:
        if snap.ok:
            result.balances_ok += 1
        else:
            result.balances_failed += 1
            result.errors.append(
                f"{snap.provider_slug}/{snap.account_key}: {snap.error or 'unknown'}"
            )
    return result


def format_balance_report(snapshots: list[ProviderBalanceSnapshot]) -> str:
    from services.provider_registry import get_provider_account_record, get_provider_record

    lines: list[str] = ["<b>💰 أرصدة المزوّدين</b>"]
    total = 0.0
    for snap in snapshots:
        account_rec = get_provider_account_record(snap.provider_slug, snap.account_key)
        provider_rec = get_provider_record(snap.provider_slug)
        display = (
            account_rec.display_name
            if account_rec and account_rec.display_name
            else snap.account_key
        )
        provider_name = provider_rec.name if provider_rec else snap.provider_slug
        label = f"{provider_name} — {display}"
        if snap.ok:
            lines.append(f"• <b>{label}</b>: <b>{snap.balance_usd:.4f}</b> {snap.currency}")
            total += snap.balance_usd
        else:
            lines.append(f"• <b>{label}</b>: ⚠️ غير متاح")
    lines.append(f"\n<b>المجموع:</b> {total:.4f} USD")
    return "\n".join(lines)
