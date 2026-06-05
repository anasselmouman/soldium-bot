"""
إعدادات مشروع SOLDIUM.
المفاتيح الحساسة تُقرأ من متغيرات البيئة (ملف .env اختياري).
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_FILE = _PROJECT_ROOT / ".env"

if _ENV_FILE.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE)
    except ImportError:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"المتغير {name} غير معرّف. أنشئ ملف .env من .env.example واملأ القيم المطلوبة."
        )
    return value


def _env_int(name: str) -> int:
    return int(_require_env(name))


# توكن البوت الخاص بـ Telegram
BOT_TOKEN = _require_env("BOT_TOKEN")

# إعدادات مزود خدمات SMM (مفتاح لكل منصة)
SMM_KEY_INSTAGRAM = _require_env("SMM_KEY_INSTAGRAM")
SMM_KEY_FACEBOOK = _require_env("SMM_KEY_FACEBOOK")
SMM_KEY_TIKTOK = _require_env("SMM_KEY_TIKTOK")
SMM_KEY_DEFAULT = _require_env("SMM_KEY_DEFAULT")
API_URL = os.environ.get("API_URL", "https://gozibra.com/api/v2").strip()

SMM_API_KEYS: dict[str, str] = {
    "instagram": SMM_KEY_INSTAGRAM,
    "facebook": SMM_KEY_FACEBOOK,
    "tiktok": SMM_KEY_TIKTOK,
    "default": SMM_KEY_DEFAULT,
}


def api_key_for_account(account_type: str) -> str:
    """يرجع مفتاح API للحساب المخزّن على الطلب (أو default)."""
    key = str(account_type or "").strip().lower()
    if key not in SMM_API_KEYS:
        key = "default"
    api_key = SMM_API_KEYS[key].strip()
    if not api_key:
        raise RuntimeError(
            f"مفتاح SMM_KEY_{key.upper()} غير معرّف. راجع ملف .env وأعد تشغيل البوت."
        )
    return api_key

# رابط الدعم الفني (محادثة تيليغرام)
SUPPORT_LINK = os.environ.get("SUPPORT_LINK", "https://t.me/SoldiumSupport").strip()

# رابط واتساب الدعم (0647503039 → 212647503039)
WHATSAPP_SUPPORT_LINK = os.environ.get(
    "WHATSAPP_SUPPORT_LINK", "https://wa.me/212647503039"
).strip()

# قناة مواد الترويج الجاهزة لشركاء الإحالة
PARTNERS_CHANNEL_LINK = os.environ.get(
    "PARTNERS_CHANNEL_LINK", "https://t.me/SoldiumPartners"
).strip()

# معرف المدير
ADMIN_ID = _env_int("ADMIN_ID")

# العملة المعروضة للمستخدمين (رصيد الحساب والطلبات بالدرهم المغربي)
CURRENCY_DISPLAY = "DH"

# تحويل USDT → رصيد المستخدم (DH) عند تأكيد شحن الكريبتو
USDT_TO_DH_RATE = 10.0
MIN_CRYPTO_DEPOSIT_USDT = 10.0
MIN_CRYPTO_WITHDRAW_DH = MIN_CRYPTO_DEPOSIT_USDT * USDT_TO_DH_RATE
MIN_PAYPAL_DEPOSIT_USD = 5.0

# عند ضبطه: يُرسل POST عند ضغط المستخدم «تأكيد الدفع» لمراقبة الإيداع تلقائياً
CRYPTO_DEPOSIT_WEBHOOK_URL = os.environ.get("CRYPTO_DEPOSIT_WEBHOOK_URL", "").strip()

# نسبة رصيد بطاقات التعبئة (70% للعميل)
RECHARGE_CREDIT_RATIO = 0.70

# حدود الشحن عند اعتماد الأدمن (بالدرهم المغربي)
MIN_DEPOSIT_DH = 5.0
MAX_SINGLE_DEPOSIT_DH = float(os.environ.get("MAX_SINGLE_DEPOSIT_DH", "50000"))

# حد أدنى لسحب أرباح الإحالة (يُطبَّق في قاعدة البيانات والواجهة)
MIN_REFERRAL_WITHDRAW_DH = 20.0

# أقصى عدد لطلبات الشحن المعلّقة لكل مستخدم في آن واحد
MAX_PENDING_DEPOSITS_PER_USER = int(os.environ.get("MAX_PENDING_DEPOSITS_PER_USER", "5"))

# حد أمان عام لأقصى كمية في الطلب (يُطبَّق مع حدود الكتالوج والمزود)
MAX_ORDER_QUANTITY_CAP = int(os.environ.get("MAX_ORDER_QUANTITY_CAP", "10000000"))

# سجلات تشخيص للمطور (main.py / payment.py) — اتركه فارغاً في الإنتاج
DEBUG_AGENT_LOGS = os.environ.get("DEBUG_AGENT_LOGS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# تحويل سعر المزوّد (USD لكل وحدة) إلى درهم في خدمات الاشتراكات per_unit
SERVICE_USD_TO_DH_MULTIPLIER = float(
    os.environ.get("SERVICE_USD_TO_DH_MULTIPLIER", "14"),
)

# هامش الربح المستخدم عند تحديث أسعار services_config (للمرجعية فقط حالياً)
MARKUP_TELEGRAM = 22
MARKUP_X = 23
MARKUP_X_VIEWS = 37
