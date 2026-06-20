# -*- coding: utf-8 -*-
"""نص قسم «نقاط هامة»."""

from utils.ui_branding import ACCOUNT_PERSISTENCE_HTML, format_breadcrumb, screen_body


def _critical_points_paragraphs() -> tuple[str, ...]:
    return (
        ACCOUNT_PERSISTENCE_HTML,
        "",
        "💠 <b>استراتيجية السعر والعدد:</b> نظراً لطبيعة تطبيقات التواصل، قد يحدث نقص طبيعي في "
        "الأعداد، وخصوصاً في خدمات المتابعين. في SOLDIUM خفضنا الأسعار لتكون «سعر جملة» حقيقي؛ "
        "بحيث حتى لو طلبت كمية إضافية لتعويض أي نقص، سيبقى التكلفة الإجمالية أقل بكثير من أي منافس في السوق.",
        "",
        "⏱️ <b>وقت التنفيذ:</b> قد يبدأ الطلب فوراً أو يتأخر بضع ساعات حسب ضغط النظام.",
        "",
        "🌐 <b>الحسابات العامة:</b> يجب أن يكون الحساب والعدادات عامة (Public) وليست خاصة قبل "
        "وأثناء الطلب.",
        "",
        "🔁 <b>عدم التكرار:</b> يرجى عدم وضع أكثر من طلب لنفس الرابط في وقت واحد حتى اكتمال الأول.",
        "",
        "🚫 <b>التعديل والإلغاء:</b> لا يمكن تعديل أو إلغاء أي طلب بعد وضعه نهائياً إلا في حال "
        "وجود خلل تقني.",
        "",
        "⚠️ <b>المسؤولية:</b> تحويل الحساب لـ «خاص» أو حذف المنشور بعد الطلب يجعل الطلب مكتملاً "
        "ولا يحق المطالبة باسترداد.",
        "",
        "🛑 <b>المحتوى المحظور:</b> يمنع الطلب للحسابات (الإباحية، السياسية، المتطرفة).",
        "",
        "✅ <b>الموافقة:</b> استكمال الطلب يعني الموافقة التلقائية على هذه الشروط.",
    )


def build_critical_points_html(
    *breadcrumb_prefix: str,
    balance: object | None = None,
    currency_display: str = "DH",
) -> str:
    trail = breadcrumb_prefix + ("اقرأ قبل الشراء",) if breadcrumb_prefix else ("اقرأ قبل الشراء",)
    bc = format_breadcrumb(*trail)
    if balance is not None:
        from utils.order_flow import format_order_balance_line

        bc = f"{bc}\n{format_order_balance_line(balance, currency_display)}"
    return screen_body(bc, *_critical_points_paragraphs())
