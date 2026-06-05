# -*- coding: utf-8 -*-
"""ملاحظات موحّدة للخدمات + إرشادات الرابط في خطوة الإدخال."""

from __future__ import annotations

import re
from html import escape

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*")

GLOBAL_SERVICE_NOTES_HTML = (
    "<b>ملاحظات:</b>\n"
    "- يُرجى التأكد من أن الحساب عام وليس خاصًا.\n"
    "- في حال وجود ضغط كبير على الخدمة، قد يحدث تغيير في السرعة ووقت البدء.\n"
    "- يُرجى عدم إنشاء طلب ثانٍ لنفس الرابط قبل اكتمال الطلب الحالي داخل النظام."
)

IPTV_WC2026_NOTES_HTML = (
    "✅ في مكان الرابط اكتب أي شيء: اسمك أو دولتك\n"
    "✅ بعد الشراء سيتم التواصل معك من قبل الدعم قد يستغرق ذلك بعض الوقت. "
    "يمكنك دائماً التواصل بنفسك مع الدعم في قسم المساعدة والدعم لمعرفة المعلومات عن طلبك\n\n"
    "استمتع بأفضل خدمة IPTV تمنحك تجربة مشاهدة متكاملة ومريحة كيفما كان الجهاز ديالك. "
    "الخدمة ديالنا كتوفّر لك آلاف القنوات العالمية بجميع التصنيفات: رياضة، أفلام، مسلسلات، "
    "وثائقيات، أطفال، أخبار… كلشي متوفر بجودة عالية وصورة واضحة.\n\n"
    "✨ <b>أهم المميزات:</b>\n"
    "• جميع القنوات العالمية: قنوات عربية، أوروبية، أمريكية، رياضية، ترفيهية، إخبارية… "
    "كل ما تحتاجه في مكان واحد.\n"
    "• أفلام ومسلسلات مُحدَّثة يوميًا: مكتبة ضخمة مع آخر الإصدارات، مترجمة وبجودات مختلفة.\n"
    "• سيرفر سريع وثابت بدون تقطعات: تجربة سلسة حتى مع الإنترنت المتوسط، "
    "بفضل سيرفرات قوية ومستقرة.\n"
    "• جودة عالية FHD / HD / 4K: استمتع بصورة نقية وصوت واضح.\n"
    "• خدمة تشتغل على جميع الأجهزة:\n"
    "  — أجهزة التلفاز الذكية (Smart TV)\n"
    "  — Android / iOS\n"
    "  — PHONE / TABLET\n"
    "  — الحواسيب\n"
    "  — أجهزة IPTV Box\n"
    "  — Chromecast / Fire Stick …\n"
    "• دعم فني متواصل: فريق جاهز لمساعدتك في أي وقت.\n\n"
    "🔶 <b>علاش تختار خدمتنا؟</b>\n"
    "لأننا كنضمنو ليك استقرار، جودة، تنوع، وثمن مناسب مع تجربة مشاهدة راقية خالية من المشاكل"
)

IPTV_WC2026_NOTES_SHORT = (
    "✅ في مكان الرابط: اسمك أو دولتك\n"
    "✅ بعد الشراء يتواصل معك الدعم — أو راسلنا من قسم المساعدة والدعم\n\n"
    "خدمة IPTV بآلاف القنوات العالمية، جودة FHD/HD/4K، وتشتغل على جميع الأجهزة."
)

IPTV_PANEL_NOTES_HTML = (
    "✅ في مكان الرابط اكتب اي شيئ اسمك او دولتك\n"
    "✅ بعد الشراء سيتواصل معك الدعم - يمكنك دائما التواصل مع الدعم بنفسك في حالة تأخر الرد .\n\n"
    "عرض حصري و خيالي 🔥🤌🏻 [ إربح من بيع حسابات IP.-TV مع Gozibra 💰]\n\n"
    "1- پانل IP.-TV خاصة بالموزيعين [الجمالة] جودة عالية سيرفر بروميوم بدون تقطيع يتوفر على جميع قنوات العالم "
    "و أحدث الافلام و المسلسلات . يشتغل على جميع الاجهزة\n\n"
    "1 كود = 1 سنة, يمكنك تقسيم الكود ( شهر، شهريين ، ستة شهور, سنة ...) مع امكانية تقديم test ل زبناءك "
    "ب شكل غير محدود ، سوف نقوم بارسال كورس يشرح لك كيف تستخدم البانل و كل شيء لاحتراف المجال.\n\n"
    "🤯 عند شراءك للبانل سوف نقوم بارسال لك :\n"
    "- البانل الخاصة بك + ڤيديو حصري وشامل لكيفية استخدام الپانل و كيفية تفعيل IP:!$TV ل زبناء\n"
    "- حساب كانڤا پرو 3 سنوات ب بريدك الالكتروني الخاص\n"
    "- رقم وتساب دعم فني حصري 24/24h يساعدك و يحل معاك اي مشكل تواجه\n"
    "- دورة حصرية و شاملة لاحتراف facebook ads من خلالها يمكنك بدأ مشروعك و اطلاق حملات اعلانية "
    "تمكنك من بيع المنتجات الرقمية.\n"
    "- دورة حصرية و شاملة لاحتراف Tiktok ads من خلالها يمكنك بدأ مشروعك و اطلاق حملات اعلانية "
    "تمكنك من بيع المنتجات الرقمية.\n"
    "- دورة حصرية لاحتراف التصميم على موقع CANVA يمكنك من تصميم تصميات جدابة لاعلاناتك و رفع مبيعاتك بشكل كبير\n"
    "- كورس حصري وشامل لاحتراف seo الدي سوف يمكنك من ارشفة موقعك او متجرك ف google و يخليك تجلب مبيعات "
    "بدون اشهار مجانا من خلال free ترافيك ( احسن مجال ممكن ان تحترفه )\n\n"
    "هده اللوحات خاصة بالموزيعين او اعادة البيع, لوحة 10 كريديت كل 1 كريديت = سنة\n\n"
    "مثلا الپانل تتوفر على 10 كريديت، كل كريدي به سنة كاملة - يمكن تقسيمها لعدة اكواد - شهر، ثلاث اشهر، "
    "ست اشهر, يمكن تحكم بها كيف ما تشاء، و الدعم الفني يكون VIP عبر الوتساب الدي سوف نقوم بارساله لك ,"
    "بعد شراء الپانل سوف نقوم باعطائك معها فيديوهات خاصة من انجازنا تشرح لك طريقة العمل بشكل احترافي . ...\n\n"
    "هده الپانل سوف تمكنك من التحكم في عملك كيف ما تشاء وسوف تصبح مثلنا تبيع اي كمية تريد لزبنائك\n\n"
    "بهده الپانل يمكنك البيع لزبنائك كيف ما تشاء لن تحتاجنا بعد الان و ويمكنك تقديم بث تجريبي لهم بعدد لا محدود\n"
    "سوف نقوم بشرح كل شيء لك لاحتراف هدا المجال بعد ان تقوم بشراء الپانل اللوحة سوف نقوم باعطائك معها "
    "فيديوهات خاصة من انجازنا تشرح لك طريقة العمل بشكل احترافي."
)

IPTV_PANEL_NOTES_SHORT = (
    "✅ في مكان الرابط: اسمك أو دولتك\n"
    "✅ بعد الشراء يتواصل معك الدعم — راسل الدعم عند تأخر الرد\n\n"
    "عرض حصري 🔥🤌🏻 — پانل IP.-TV للموزيعين مع Gozibra 💰\n"
    "(التفاصيل الكاملة في الرسالة — اختر الخدمة للمتابعة)"
)

IPTV_PANEL_SERVICE_NOTICE = "📜 <b>اقرأ الملاحظات في الرسالة أعلاه</b> قبل إتمام الطلب."

_LINK_PROMPTS: dict[str, tuple[str, bool]] = {
    "x_live_broadcast": (
        "يرجى إرسال رابط البث المباشر (Live Link) الجاري حالياً.",
        False,
    ),
    "x_direct_messages": (
        "أرسل نص الطلب على سطرين:\n\n"
        "السطر 1 (LINK): رابط حسابك على X أو @yourusername\n\n"
        "السطر 2 (USERNAMES): من 5 إلى 10 يوزرات مفصولة بفواصل. مثال:\n"
        "<code>elonmusk, dogecoin, Nike, tesla</code>\n\n"
        "تجنّب الحسابات المقفلة خصوصياً (Private).",
        False,
    ),
    "x_spaces": (
        "أرسل رابط السبيس فقط. يجب أن يحتوي الرابط على كلمة <code>spaces</code>.",
        False,
    ),
}


def format_notice_html(note: str) -> str:
    """عرض ملاحظة بأمان في parse_mode=HTML."""
    text = str(note or "").strip()
    if not text:
        return ""
    if _HTML_TAG_RE.search(text):
        return text
    escaped = escape(text)
    return _BOLD_MD_RE.sub(r"<b>\1</b>", escaped)


def _section_notice_key(section_key: str | None, section: dict | None = None) -> str:
    sk = str(section_key or "").strip()
    if sk:
        return sk
    if section:
        return str(section.get("section_notice_key") or "").strip()
    return ""


def _is_iptv_wc2026_section(section_key: str | None, section: dict | None = None) -> bool:
    return _section_notice_key(section_key, section) == "iptv_wc2026"


def _is_iptv_panel_section(section_key: str | None, section: dict | None = None) -> bool:
    return _section_notice_key(section_key, section) == "iptv_panel"


def resolve_service_notice(service: dict, *, short: bool = False) -> str:
    """ملاحظات الخدمة حسب القسم."""
    sk = _section_notice_key(str(service.get("section_key") or ""))
    if sk == "iptv_wc2026":
        return IPTV_WC2026_NOTES_SHORT if short else IPTV_WC2026_NOTES_HTML
    if sk == "iptv_panel":
        return IPTV_PANEL_SERVICE_NOTICE
    return GLOBAL_SERVICE_NOTES_HTML


def resolve_section_notice(
    section: dict,
    *,
    platform_key: str | None = None,
    short: bool = False,
) -> str:
    """ملاحظات شاشة اختيار الخدمات داخل القسم."""
    pk = str(platform_key or "").strip()
    if _is_iptv_panel_section(None, section):
        return IPTV_PANEL_NOTES_SHORT if short else IPTV_PANEL_NOTES_HTML
    if pk == "subscriptions":
        if _is_iptv_wc2026_section(None, section):
            return IPTV_WC2026_NOTES_SHORT if short else IPTV_WC2026_NOTES_HTML
        return ""
    if _is_iptv_wc2026_section(None, section):
        return IPTV_WC2026_NOTES_SHORT if short else IPTV_WC2026_NOTES_HTML
    return GLOBAL_SERVICE_NOTES_HTML


def resolve_link_prompt(
    platform_key: str,
    section_key: str | None,
    subsection_key: str | None = None,
    *,
    service: dict | None = None,
) -> tuple[str | None, bool, bool]:
    """نص إرشاد الرابط + هل يُقبل @username + هل يُقبل نصاً حراً."""
    svc = service or {}
    pk = str(platform_key or "").strip()
    sk = str(section_key or "").strip()
    if pk == "subscriptions" and sk in {"iptv_wc2026", "iptv_panel"}:
        return (
            "✅ في مكان الرابط اكتب أي شيء: اسمك أو دولتك.",
            False,
            True,
        )
    key = str(svc.get("link_prompt_key") or "").strip()
    if key and key in _LINK_PROMPTS:
        text, allow_username = _LINK_PROMPTS[key]
        return text, allow_username, False

    sk = sk or None
    ssk = str(subsection_key or "").strip() or None

    if pk == "telegram" and ssk == "future_posts":
        return (
            "أرسل رابط <b>آخر منشور</b> في القناة (مثال: https://t.me/username/123). "
            "لن تُطبَّق المشاهدات على هذا المنشور، بل على المنشورات القادمة فقط.",
            False,
            False,
        )
    if pk == "telegram" and ssk == "past_posts":
        return (
            "أرسل رابط <b>القناة العام</b> فقط (مثال: https://t.me/username أو @username). "
            "لا تضع رابط منشور فردي.",
            True,
            False,
        )
    if pk == "telegram" and sk in {"members", "channel_members"}:
        return "أرسل رابط القناة أو المجموعة التي تريد إضافة الأعضاء إليها.", True, False
    if pk == "telegram" and sk == "post_share":
        return "أرسل رابط المنشور الذي تريد زيادة المشاركات عليه.", False, False
    if pk == "telegram" and sk == "start_bot":
        return "أرسل رابط البوت الذي تريد تشغيل الخدمة عليه.", False, False
    if pk == "telegram" and sk == "automatic_interactions":
        return (
            "أرسل رابط آخر منشور في القناة (مثال: https://t.me/username/123) لتحديد الحساب. "
            "لن تُطبَّق المشاهدات على هذا المنشور، بل على المنشورات الجديدة فقط.",
            True,
            False,
        )
    if pk == "x" and sk == "followers":
        return "أرسل رابط حسابك على X (تويتر) أو المعرف الخاص بك.", True, False
    if pk == "x" and sk == "likes":
        return "أرسل رابط التغريدة التي تريد زيادة الإعجابات عليها.", False, False
    if pk == "x" and sk in {"views", "video_views"}:
        return "أرسل رابط التغريدة التي تحتوي على الفيديو المطلوب زيادة مشاهداته.", False, False
    if pk == "x" and sk == "mentions":
        return (
            "أرسل رابط التغريدة لتنفيذ خدمة المنشن عليها. "
            "يجب أن يحتوي الرابط على <code>/status/</code>."
        ), False, False
    if pk == "x" and sk == "direct_messages":
        text, allow = _LINK_PROMPTS["x_direct_messages"]
        return text, allow, False
    if pk == "x" and sk == "spaces":
        text, allow = _LINK_PROMPTS["x_spaces"]
        return text, allow, False
    if pk == "x" and sk == "live_broadcast":
        text, allow = _LINK_PROMPTS["x_live_broadcast"]
        return text, allow, False

    return None, False, False
