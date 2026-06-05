# -*- coding: utf-8 -*-

"""Referral commission rules, tier definitions, and upgrade messaging (SOLDIUM)."""



from __future__ import annotations



import re

from typing import Final, TypedDict



from utils.money import to_float



MAX_REFERRAL_LEVEL: Final[int] = 4



# حالات الطلب التي تُظهر تقدير الأرباح المعلّقة فوراً (عرض فقط — لا تُضاف إلى referral_balance)

PENDING_COMMISSION_STATUSES = (

    "pending",

    "pending_admin",

    "submitted",

    "in progress",

    "processing",

)

# اسم قديم — نفس المجموعة (مستخدم في database.py)

IN_PROGRESS_ORDER_STATUSES = PENDING_COMMISSION_STATUSES



_REF_PREFIX = re.compile(r"(?i)^ref[_]?(\d+)$")





class ReferralTierSpec(TypedDict):

    name: str

    rate: float

    active_users: int

    earnings: float





REFERRAL_TIERS: dict[int, ReferralTierSpec] = {

    1: {"name": "ناشر", "rate": 0.10, "active_users": 0, "earnings": 0.0},

    2: {"name": "محترف", "rate": 0.15, "active_users": 10, "earnings": 150.0},

    3: {"name": "خبير", "rate": 0.20, "active_users": 25, "earnings": 500.0},

    4: {"name": "شريك", "rate": 0.25, "active_users": 50, "earnings": 1500.0},

}



PARTNER_LEVEL_UPGRADE_HTML: Final[str] = (

    "🎉🏆 إنجاز تاريخي! أهلاً بك في القمة 🏆🎉\n"

    "━━━━━━━━━━━━━━━\n"

    "لقد أثبتّ جدارتك وتفوقك بشكل استثنائي!\n"

    "تمت ترقية حسابك رسمياً إلى أعلى مستوى ممكن في نظامنا:\n"

    "👑 <b>[ شريك Soldium ]</b> 👑\n\n"

    "ابتداءً من هذه اللحظة، عمولتك أصبحت <b>25%</b> على كل طلب!\n"

    "💡 <b>بكل شفافية:</b> نحن نربح 50% من قيمة الطلبات، ومنحك 25% يعني أننا "

    "<b>نتقاسم الأرباح معك بالنصف تماماً</b> (50/50).\n\n"

    "أنت لم تعد مجرد مسوق، أنت الآن <b>شريك حقيقي</b> ونجاحك هو نجاحنا. استمر في التألق! 🚀💸"

)





def _clamp_level(level: int) -> int:

    try:

        n = int(level)

    except (TypeError, ValueError):

        return 1

    return max(1, min(MAX_REFERRAL_LEVEL, n))





def referral_level_name(level: int) -> str:

    return REFERRAL_TIERS[_clamp_level(level)]["name"]





def commission_rate(level: int) -> float:

    return REFERRAL_TIERS[_clamp_level(level)]["rate"]





def commission_rate_percent(level: int) -> int:

    return int(round(commission_rate(level) * 100))





def net_spent_amount(order_amount: float, refunded_amount: float | None) -> float:

    gross = to_float(order_amount)

    ref = to_float(refunded_amount)

    if ref < 0:

        ref = 0.0

    if ref > gross:

        ref = gross

    return max(0.0, gross - ref)





def compute_commission(net_spent: float, level: int) -> float:

    return round(to_float(net_spent) * commission_rate(level), 6)





def next_level_spec(current_level: int) -> ReferralTierSpec | None:

    level = _clamp_level(current_level)

    if level >= MAX_REFERRAL_LEVEL:

        return None

    return REFERRAL_TIERS[level + 1]





def meets_next_level_requirements(

    current_level: int,

    *,

    active_users: int,

    earnings: float,

) -> bool:

    nxt = next_level_spec(current_level)

    if nxt is None:

        return False

    return active_users >= nxt["active_users"] and to_float(earnings) >= nxt["earnings"]





def format_standard_level_upgrade_message(new_level: int) -> str:

    """رسالة تهنئة للمستويات 2 و 3 (المستوى 4 له رسالة منفصلة)."""

    level = _clamp_level(new_level)

    spec = REFERRAL_TIERS[level]

    name = spec["name"]

    pct = int(round(spec["rate"] * 100))



    if level == 2:

        return (

            "🎉 <b>مبروك الترقية!</b>\n"

            "━━━━━━━━━━━━━━━\n"

            f"تمت ترقية حسابك رسمياً إلى مستوى «<b>{name}</b>» 🏅\n\n"

            f"عمولتك الدائمة أصبحت <b>{pct}%</b> على كل طلب مؤكد من مدعويك!\n\n"

            "استمر في دعوة أصدقائك ومتابعيك — كل طلب جديد يقربك من المستوى التالي. 🚀"

        )

    if level == 3:

        return (

            "🎉 <b>إنجاز رائع!</b>\n"

            "━━━━━━━━━━━━━━━\n"

            f"وصلت إلى مستوى «<b>{name}</b>» — أنت الآن من أفضل المسوقين لدينا! ⭐\n\n"

            f"عمولتك الدائمة ارتفعت إلى <b>{pct}%</b> على كل طلب مؤكد.\n\n"

            "بقي خطوة واحدة فقط للوصول إلى أعلى مستوى: <b>شريك Soldium</b>. واصل التألق! 💪"

        )



    return (

        f"🎉 <b>تهانينا!</b>\n"

        f"تم ترقية مستواك إلى «<b>{name}</b>» — "

        f"عمولتك الدائمة أصبحت <b>{pct}%</b> على كل طلب مؤكد من مدعويك! 🚀"

    )





def format_level_upgrade_notification(new_level: int) -> str:

    if _clamp_level(new_level) == MAX_REFERRAL_LEVEL:

        return PARTNER_LEVEL_UPGRADE_HTML

    return format_standard_level_upgrade_message(new_level)





async def flush_pending_referral_level_upgrade_notifications(bot) -> None:

    """إرسال إشعارات الترقية المعلّقة (بعد دفع عمولة ناجحة)."""

    import logging



    from database import pop_all_pending_referral_level_upgrades

    from utils.smart_notifications import send_smart_notification



    logger = logging.getLogger(__name__)

    pending = pop_all_pending_referral_level_upgrades()

    for user_id, new_level in pending:

        try:

            await send_smart_notification(

                bot,

                user_id,

                format_level_upgrade_notification(new_level),

            )

        except Exception as exc:

            logger.warning(

                "Referral level upgrade notify failed for user %s level %s: %s",

                user_id,

                new_level,

                exc,

            )





def parse_referrer_id_from_start_payload(payload: str | None) -> int | None:

    """

    Accepts Telegram /start deep-link payload:

    - "12345" or "ref_12345" / "ref12345" (case-insensitive ref_ prefix).

    """

    if payload is None:

        return None

    raw = str(payload).strip()

    if not raw:

        return None

    m = _REF_PREFIX.match(raw)

    if m:

        return int(m.group(1))

    if raw.isdigit():

        return int(raw)

    return None

