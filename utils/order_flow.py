from html import escape
import re
from urllib.parse import urlparse

from utils.money import format_amount, format_amount_2
from utils.notices import format_notice_html, resolve_service_notice

_COMMA_SPLIT_RE = re.compile(r"[,،]\s*")
_X_USERNAME_RE = re.compile(r"^@([A-Za-z0-9_]{1,15})$")
from utils.ui_branding import format_breadcrumb

ORDER_FLOW_ROOT = "الخدمات والأسعار"


def format_order_breadcrumb(*trail: str) -> str:
    """مثال: format_order_breadcrumb('إنستغرام', 'المتابعين')"""
    return format_breadcrumb(ORDER_FLOW_ROOT, *trail)


def format_order_balance_line(balance: object, currency_display: str) -> str:
    return f"الرصيد: <b>{format_amount_2(balance)} {currency_display}</b>"


def format_order_flow_header(balance: object, currency_display: str, *trail: str) -> str:
    """مسار الخدمات والأسعار مع سطر الرصيد تحته مباشرة."""
    return (
        f"{format_order_breadcrumb(*trail)}\n"
        f"{format_order_balance_line(balance, currency_display)}"
    )


def with_order_breadcrumb(body: str, *trail: str) -> str:
    return f"{format_order_breadcrumb(*trail)}\n\n{body}"

DEFAULT_SERVICE_LINK_CTA = "أرسل رابط الحساب أو المنشور الذي تريد تنفيذ الطلب عليه:"


PLATFORM_HOST_RULES: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com", "instagr.am"),
    "facebook": ("facebook.com", "fb.watch", "fb.com"),
    "tiktok": ("tiktok.com", "vt.tiktok.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "telegram": ("t.me", "telegram.me"),
    "x": ("x.com", "twitter.com", "mobile.twitter.com"),
}

PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "instagram": "إنستغرام",
    "facebook": "فيسبوك",
    "tiktok": "تيك توك",
    "youtube": "يوتيوب",
    "telegram": "تيليجرام",
    "x": "X (تويتر)",
}


def parse_subsection_callback(callback_data: str) -> tuple[str, str, str] | None:
    parts = callback_data.strip().split(":")
    if len(parts) != 5:
        return None
    prefix, token, platform_key, section_key, subsection_key = parts
    valid_long = (prefix == "order" and token == "subsection")
    valid_short = (prefix == "o" and token == "ss")
    if not (valid_long or valid_short):
        return None
    if not platform_key or not section_key or not subsection_key:
        return None
    return platform_key.strip(), section_key.strip(), subsection_key.strip()


def build_subsections_preview(section: dict) -> str:
    lines = ["▬▬▬▬▬▬▬▬▬▬▬▬"]
    for subsection in (section.get("subsections") or {}).values():
        lines.append("")
        lines.append(str(subsection.get("title", "فرعي")))
        lines.append("▬▬▬▬▬▬▬▬▬▬▬▬")

    for item in section.get("items") or []:
        lines.append("")
        lines.append(str(item.get("name", "خدمة مباشرة")))
        lines.append("▬▬▬▬▬▬▬▬▬▬▬▬")
    return "\n".join(lines)


def format_service_price_per_1000(service: dict, currency_display: str) -> str:
    price_clean = format_amount(service["price"])
    currency = currency_display.strip() or "DH"
    unit_label = "1" if service.get("price_per_unit") else "1000"
    return f"{price_clean} {currency} لكل {unit_label}"


def order_input_step_total(service: dict) -> int:
    """عدد خطوات الإدخال النصي: رابط فقط، أو رابط + كمية."""
    return 1 if service.get("auto_quantity") is not None else 2


def build_order_progress_block(
    *,
    service_name: str | None = None,
    link: str | None = None,
    quantity: int | None = None,
    step_current: int,
    step_total: int,
    next_step: str,
) -> str:
    lines = ["<b>ملخص طلبك حتى الآن</b>"]
    if service_name:
        lines.append(f"• الخدمة: <b>{escape(str(service_name))}</b>")
    if link and link.strip():
        short = link.strip()
        if len(short) > 80:
            short = short[:77] + "..."
        lines.append(f"• الرابط: <code>{escape(short)}</code>")
    if quantity is not None:
        lines.append(f"• الكمية: <b>{quantity}</b>")
    lines.append("")
    lines.append(f"<b>الخطوة {step_current} من {step_total}:</b> {escape(next_step)}")
    return "\n".join(lines)


def format_service_note_html(note: str) -> str:
    """عرض ملاحظة الخدمة بأمان في parse_mode=HTML."""
    return format_notice_html(note)


def build_quantity_limits_text(min_quantity: int, max_quantity: int) -> str:
    return (
        f"الحد الأدنى للطلب: يمكنك طلب <b>{min_quantity}</b> أو أكثر.\n"
        f"الحد الأقصى للطلب: لا يمكنك طلب أكثر من <b>{max_quantity}</b> في طلب واحد."
    )


def build_quantity_limits_compact_text(min_quantity: int, max_quantity: int) -> str:
    return (
        f"الحد الأدنى: <b>{min_quantity}</b>\n"
        f"الحد الأقصى: <b>{max_quantity}</b>"
    )


def _service_link_cta(link_prompt: str | None) -> str:
    text = (link_prompt or "").strip() or DEFAULT_SERVICE_LINK_CTA
    return f"🔗 {text}"


def build_service_summary_text(
    service: dict,
    currency_display: str,
    min_quantity: int,
    max_quantity: int,
    fixed_quantity: int | None = None,
    *,
    breadcrumb_line: str | None = None,
) -> str:
    """ملخص الخدمة للرسالة الحية العلوية — بدون طلب إدخال (يُطلب في رسالة خطوة صغيرة)."""
    price_line = format_service_price_per_1000(service, currency_display)
    lines = [
        "<b>تفاصيل الخدمة التي اخترتها</b>",
        f"اسم الخدمة: <b>{escape(str(service['name']))}</b>",
        "",
        f"الثمن: <b>{price_line}</b>",
    ]
    if fixed_quantity is None:
        lines.extend(["", build_quantity_limits_text(min_quantity, max_quantity)])
    else:
        lines.extend(["", f"الكمية في هذه الخدمة ثابتة: <b>{fixed_quantity}</b>."])
    notice_text = resolve_service_notice(service, short=False)
    if notice_text:
        lines.extend(["", format_service_note_html(notice_text)])
    lines.append("")
    lines.append("<b>اتبع التعليمات في الرسالة أدناه لإكمال الطلب.</b>")
    body = "\n".join(lines)
    if breadcrumb_line:
        return f"{breadcrumb_line}\n\n{body}"
    return body


def build_service_summary_text_short(
    service: dict,
    currency_display: str,
    min_quantity: int,
    max_quantity: int,
    *,
    fixed_quantity: int | None = None,
    breadcrumb_line: str | None = None,
) -> str:
    """نسخة مختصرة للرسالة الحية مع صورة (حد التعليق)."""
    price_line = format_service_price_per_1000(service, currency_display)
    lines = [
        "<b>تفاصيل الخدمة التي اخترتها</b>",
        f"اسم الخدمة: <b>{escape(str(service['name']))}</b>",
        f"الثمن: <b>{price_line}</b>",
    ]
    if fixed_quantity is None:
        lines.append(f"الحد الأدنى: <b>{min_quantity}</b> | الحد الأقصى: <b>{max_quantity}</b>")
    else:
        lines.append(f"الكمية ثابتة: <b>{fixed_quantity}</b>")
    notice_text = resolve_service_notice(service, short=True)
    if notice_text:
        lines.extend(["", format_service_note_html(notice_text)])
    lines.append("<b>اتبع التعليمات في الرسالة أدناه.</b>")
    body = "\n".join(lines)
    if breadcrumb_line:
        return f"{breadcrumb_line}\n\n{body}"
    return body


def build_link_step_prompt(
    link_prompt: str | None = None,
    *,
    allow_username: bool = False,
    allow_free_text: bool = False,
    step_label: str = "الخطوة 1 من 2: الرابط",
) -> str:
    lines = [
        f"<b>{step_label}</b>",
        "",
        _service_link_cta(link_prompt),
    ]
    if allow_free_text:
        lines.append("اكتب أي نص تريده — مثال: اسمك أو دولتك.")
    elif allow_username:
        lines.append(
            "يمكنك إرسال رابط عام أو معرف بصيغة <code>@username</code> إذا كان الحساب أو القناة عامة."
        )
    return "\n".join(lines)


def build_quantity_step_prompt(
    service: dict,
    link: str,
    min_quantity: int,
    max_quantity: int,
    currency_display: str,
    *,
    step_label: str = "الخطوة 2 من 2: الكمية",
) -> str:
    link_short = link.strip()
    if len(link_short) > 80:
        link_short = link_short[:77] + "..."
    price_line = format_service_price_per_1000(service, currency_display)
    return (
        f"<b>{step_label}</b>\n\n"
        f"• الخدمة: <b>{escape(str(service['name']))}</b>\n"
        f"• الرابط المحفوظ: <code>{escape(link_short)}</code>\n\n"
        f"{build_quantity_limits_compact_text(min_quantity, max_quantity)}\n\n"
        "<b>أرسل الكمية التي تريد طلبها</b>\n"
        f"الثمن: <b>{price_line}</b>\n"
        "اكتب رقماً فقط، مثال: <code>1000</code>."
    )


def build_service_intro_text(
    service: dict,
    currency_display: str,
    min_quantity: int,
    max_quantity: int,
    fixed_quantity: int | None = None,
    link_prompt: str | None = None,
    allow_username: bool = False,
    *,
    breadcrumb_line: str | None = None,
) -> str:
    price_line = format_service_price_per_1000(service, currency_display)
    lines = [
        "<b>تفاصيل الخدمة التي اخترتها</b>",
        f"اسم الخدمة: <b>{escape(str(service['name']))}</b>",
        "",
        f"الثمن: <b>{price_line}</b>",
    ]
    if fixed_quantity is None:
        lines.extend(["", build_quantity_limits_text(min_quantity, max_quantity)])
    else:
        lines.extend(["", f"الكمية في هذه الخدمة ثابتة: <b>{fixed_quantity}</b>."])

    notice_text = resolve_service_notice(service, short=False)
    if notice_text:
        lines.extend(["", format_service_note_html(notice_text)])

    lines.extend(["", "<b>الخطوة التالية</b>", _service_link_cta(link_prompt)])
    if allow_username:
        lines.append("يمكنك إرسال رابط عام أو معرف بصيغة <code>@username</code> إذا كان الحساب أو القناة عامة.")
    else:
        lines.append("أرسل الرابط كاملاً، ويجب أن يبدأ بـ <code>http://</code> أو <code>https://</code>.")
    body = "\n".join(lines)
    if breadcrumb_line:
        return f"{breadcrumb_line}\n\n{body}"
    return body


def build_service_intro_text_short(
    service: dict,
    currency_display: str,
    min_quantity: int,
    max_quantity: int,
    *,
    fixed_quantity: int | None = None,
    link_prompt: str | None = None,
    breadcrumb_line: str | None = None,
) -> str:
    """نسخة مختصرة لتعليق رسالة الصورة الرئيسية (حد 1024)."""
    price_line = format_service_price_per_1000(service, currency_display)
    link_cta = _strip_tags_for_short(_service_link_cta(link_prompt))
    lines = [
        "<b>تفاصيل الخدمة التي اخترتها</b>",
        f"اسم الخدمة: <b>{escape(str(service['name']))}</b>",
        f"الثمن: <b>{price_line}</b>",
    ]
    if fixed_quantity is None:
        lines.append(f"الحد الأدنى للطلب: <b>{min_quantity}</b>")
        lines.append(f"الحد الأقصى للطلب: <b>{max_quantity}</b>")
    else:
        lines.append(f"الكمية ثابتة: <b>{fixed_quantity}</b>")
    lines.extend(["", f"الخطوة التالية: {link_cta}"])
    body = "\n".join(lines)
    if breadcrumb_line:
        return f"{breadcrumb_line}\n\n{body}"
    return body


def _strip_tags_for_short(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def build_invoice_text(
    service: dict,
    currency_display: str,
    quantity: int,
    total_price: object,
    link: str,
    is_fixed_quantity: bool = False,
    *,
    breadcrumb_line: str | None = None,
) -> str:
    quantity_text = f"{quantity} (ثابتة تلقائياً)" if is_fixed_quantity else str(quantity)
    link_display = escape(link.strip()) if link.strip() else "—"
    price_line = format_service_price_per_1000(service, currency_display)
    notice_text = resolve_service_notice(service, short=False)
    notice_block = ""
    if notice_text:
        notice_block = f"\n\n{format_service_note_html(notice_text)}"
    body = (
        "<b>مراجعة الطلب قبل التأكيد</b>\n"
        "تأكد من أن كل المعلومات صحيحة قبل الضغط على زر تأكيد الطلب.\n"
        "<blockquote>"
        f"اسم الخدمة: <b>{escape(str(service['name']))}</b>\n"
        f"الرابط: <code>{link_display}</code>\n"
        f"الكمية: <b>{quantity_text}</b>\n"
        f"ثمن الخدمة: <b>{price_line}</b>\n"
        f"المبلغ الذي سيتم خصمه: <b>{format_amount(total_price)} {currency_display}</b>"
        "</blockquote>"
        f"{notice_block}\n\n"
        "بعد التأكيد يبدأ تنفيذ الطلب ولا يمكن تعديل الرابط أو الكمية من هنا."
    )
    if breadcrumb_line:
        return f"{breadcrumb_line}\n\n{body}"
    return body


def _platform_mismatch_error(platform_key: str) -> str:
    name = PLATFORM_DISPLAY_NAMES.get(platform_key, platform_key)
    return f"الرابط غير صحيح لهذه الخدمة. أرسل رابطاً تابعاً لمنصة <b>{name}</b>."


def _link_matches_platform(raw: str, platform_key: str) -> bool:
    """تحقق بسيط: وجود نطاق المنصة في النص."""
    lowered = raw.strip().lower()
    if not lowered:
        return False

    keywords = PLATFORM_HOST_RULES.get(platform_key, ())
    if keywords and any(keyword in lowered for keyword in keywords):
        return True

    if platform_key == "telegram" and re.fullmatch(r"@[A-Za-z0-9_]{3,}", raw.strip()):
        return True
    if platform_key == "x" and _X_USERNAME_RE.fullmatch(raw.strip()):
        return True

    return not keywords


def _is_http_url(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _validate_x_direct_messages(raw: str) -> tuple[bool, str]:
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return (
            False,
            "أرسل الطلب على <b>سطرين</b>:\n"
            "السطر 1: رابط حسابك على X أو @yourusername\n"
            "السطر 2: من 5 إلى 10 يوزرات مفصولة بفواصل.",
        )
    first_line = lines[0]
    if not (_link_matches_platform(first_line, "x") or _X_USERNAME_RE.fullmatch(first_line)):
        return False, "السطر الأول يجب أن يكون رابط X أو @username لحسابك."
    usernames = [u.strip().lstrip("@") for u in _COMMA_SPLIT_RE.split(lines[1]) if u.strip()]
    if len(usernames) < 5 or len(usernames) > 10:
        return (
            False,
            "السطر الثاني يجب أن يحتوي على <b>من 5 إلى 10</b> يوزرات مفصولة بفاصلة.",
        )
    for user in usernames:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", user):
            return False, f"يوزر غير صالح: <code>{escape(user)}</code>"
    return True, ""


def _is_x_live_broadcast_url(lowered: str) -> bool:
    """رابط بث مباشر على X — مسارات البث الرسمية فقط."""
    return "/i/broadcasts/" in lowered or "/i/broadcast/" in lowered


def _telegram_path_parts(raw: str) -> list[str] | None:
    """أجزاء مسار t.me/telegram.me بعد النطاق."""
    first = raw.splitlines()[0].strip()
    if first.startswith("@"):
        user = first.lstrip("@")
        return [user] if re.fullmatch(r"[A-Za-z0-9_]{3,}", user) else None
    if not _is_http_url(first):
        return None
    lowered = first.lower()
    if "t.me" not in lowered and "telegram.me" not in lowered:
        return None
    path = urlparse(first).path.strip("/")
    return [p for p in path.split("/") if p] or None


def _is_telegram_post_url(raw: str) -> bool:
    """رابط منشور: t.me/user/123 أو t.me/c/1234567890/42."""
    parts = _telegram_path_parts(raw)
    if not parts:
        return False
    if parts[0] == "c":
        return len(parts) >= 3 and parts[-1].isdigit()
    return len(parts) >= 2 and parts[-1].isdigit()


def _is_telegram_channel_url(raw: str) -> bool:
    """رابط قناة/مجموعة عامة بدون رقم منشور."""
    parts = _telegram_path_parts(raw)
    if not parts:
        return False
    if parts[0] == "c":
        return len(parts) == 2 and parts[1].isdigit()
    if len(parts) == 1:
        return not parts[0].isdigit()
    return False


def _telegram_link_mode(section_key: str | None, subsection_key: str | None) -> str | None:
    """post = رابط منشور | channel = رابط قناة فقط."""
    sk = str(section_key or "").strip()
    ssk = str(subsection_key or "").strip()
    if sk == "automatic_interactions":
        return "post"
    if sk == "post_share":
        return "post"
    if ssk == "future_posts":
        return "post"
    if ssk == "past_posts":
        return "channel"
    return None


def _validate_section_link_rules(
    raw: str,
    platform_key: str,
    section_key: str | None,
    subsection_key: str | None = None,
    *,
    service: dict | None = None,
    service_id: str | None = None,
) -> tuple[bool, str]:
    sk = str(section_key or "").strip()
    ssk = str(subsection_key or "").strip()
    svc = service or {}

    if platform_key == "telegram":
        link_mode = _telegram_link_mode(sk or None, ssk or None)
        if link_mode == "post" and not _is_telegram_post_url(raw):
            return (
                False,
                "أرسل <b>رابط منشور</b> بصيغة <code>https://t.me/username/123</code> "
                "(أو <code>https://t.me/c/...</code> للقنوات الخاصة). "
                "رابط القناة وحده غير كافٍ لهذه الخدمة.",
            )
        if link_mode == "channel" and not _is_telegram_channel_url(raw):
            return (
                False,
                "أرسل <b>رابط القناة العام</b> فقط (مثال: <code>https://t.me/username</code>) "
                "وليس رابط منشور فردي.",
            )

    if platform_key == "x" and sk == "direct_messages":
        return _validate_x_direct_messages(raw)

    if platform_key == "x" and sk == "mentions":
        if "/status/" not in raw.lower():
            return (
                False,
                "يجب أن يحتوي الرابط على <code>/status/</code> (رابط التغريدة وليس الحساب فقط).",
            )

    if platform_key == "x" and sk == "spaces":
        if "spaces" not in raw.lower():
            return False, "أرسل رابط السبيس فقط. يجب أن يحتوي الرابط على كلمة <code>spaces</code>."

    if platform_key == "x" and sk == "live_broadcast":
        lowered = raw.lower()
        if not _is_x_live_broadcast_url(lowered):
            return (
                False,
                "أرسل رابط البث المباشر الجاري. يجب أن يحتوي على <code>/i/broadcasts/</code> أو مسار بث صالح.",
            )

    needs_comment_link = svc.get("link_type") == "comment" or str(service_id or "") == "4371"
    if needs_comment_link:
        lowered = raw.lower()
        if "/comment" not in lowered and "comment_id" not in lowered:
            return (
                False,
                "⚠️ ضع <b>رابط التعليق</b> وليس رابط الفيديو. "
                "من المتصفح على الحاسوب: اضغط تاريخ التعليق وانسخ الرابط.",
            )

    return True, ""


def validate_platform_link(
    link: str,
    platform_key: str,
    section_key: str | None = None,
    subsection_key: str | None = None,
    *,
    service: dict | None = None,
    service_id: str | None = None,
    allow_username: bool = False,
) -> tuple[bool, str]:
    """التحقق من الرابط حسب المنصة والقسم."""
    _ = subsection_key
    raw = (link or "").strip()
    if not raw:
        return False, _platform_mismatch_error(platform_key)

    sk = str(section_key or "").strip()
    if platform_key == "subscriptions" and sk in {"iptv_wc2026", "iptv_panel"}:
        return True, ""
    is_x_dm = platform_key == "x" and sk == "direct_messages"

    if not is_x_dm:
        first_line = raw.splitlines()[0].strip()
        if allow_username:
            if not (
                _is_http_url(first_line)
                or (platform_key == "telegram" and re.fullmatch(r"@[A-Za-z0-9_]{3,}", first_line))
                or (platform_key == "x" and _X_USERNAME_RE.fullmatch(first_line))
            ):
                return (
                    False,
                    "أرسل رابطاً يبدأ بـ <code>http://</code> أو <code>https://</code> "
                    "أو معرفاً بصيغة <code>@username</code>.",
                )
        elif not _is_http_url(first_line):
            return (
                False,
                "أرسل الرابط كاملاً، ويجب أن يبدأ بـ <code>http://</code> أو <code>https://</code>.",
            )

    check_text = raw.splitlines()[0].strip() if is_x_dm else raw
    if not _link_matches_platform(check_text, platform_key):
        return False, _platform_mismatch_error(platform_key)

    return _validate_section_link_rules(
        raw,
        platform_key,
        section_key,
        subsection_key,
        service=service,
        service_id=service_id or (str(service.get("id", "")) if service else None),
    )
