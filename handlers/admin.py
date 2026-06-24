import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID
from database import (
    OrderRecord,
    WithdrawalRecord,
    add_user,
    approve_withdrawal_by_admin,
    count_orders,
    count_pending_admin_orders,
    count_pending_deposits,
    count_pending_withdrawals,
    count_users,
    get_all_user_ids,
    get_last_orders,
    get_order_id_by_provider_ref,
    get_pending_admin_orders_ordered,
    get_pending_withdrawals_ordered,
    get_user,
    get_withdrawal,
    reject_withdrawal_by_admin,
    set_order_status_by_admin,
    set_user_referral_level,
    get_method_withdrawal_ledger_summary,
    sum_revenue,
)
from services.order_provider_sync import user_visible_order_ref
from keyboards.admin import (
    build_admin_manual_order_actions,
    build_admin_menu,
    build_admin_withdrawal_actions,
)
from utils.withdraw_details import format_withdraw_details_admin_lines, safe_withdraw_details
from services.provider_ops import fetch_all_provider_balances, format_balance_report
from utils.smart_notifications import send_smart_notification
from utils.money import format_dh
from services.referral import PARTNER_LEVEL_UPGRADE_HTML as PARTNER_WELCOME_HTML
from utils.states import AdminFlow

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "admin:system_balance")
async def system_balance_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    try:
        snapshots = await fetch_all_provider_balances()
        if not snapshots:
            await callback.message.answer(
                "⚠️ لا توجد حسابات مزوّد نشطة. راجع جداول providers و provider_accounts."
            )
        else:
            await callback.message.answer(format_balance_report(snapshots))
    except Exception as exc:
        logger.exception("Failed system balance fetch: %s", exc)
        await callback.message.answer("⚠️ تعذر جلب الرصيد المتاح للنظام الآن.")
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def stats_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    await callback.message.answer(
        "<b>📊 إحصائيات النظام</b>\n"
        f"• المستخدمون: <b>{count_users()}</b>\n"
        f"• الطلبات: <b>{count_orders()}</b>\n"
        f"• طلبات الشحن المعلقة: <b>{count_pending_deposits()}</b>\n"
        f"• طلبات السحب المعلقة: <b>{count_pending_withdrawals()}</b>\n"
        f"• طلبات بانتظار الأدمن: <b>{count_pending_admin_orders()}</b>\n"
        f"• إجمالي المبيعات: <code>{format_dh(sum_revenue())}</code>"
    )
    await callback.answer()


@router.callback_query(F.data == "admin:last_orders")
async def last_orders_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    orders = get_last_orders(limit=10)
    if not orders:
        await callback.message.answer("<b>لا توجد طلبات بعد.</b>")
        await callback.answer()
        return
    lines = ["<b>🧾 آخر 10 طلبات</b>"]
    for order in orders:
        prov = str(order.get("provider_order_id") or "").strip() or "—"
        lines.append(
            f"\n• رقم الطلب: <code>{escape(prov)}</code> | user: <code>{order['user_id']}</code>\n"
            f"status: <b>{escape(str(order['status']))}</b> | price: <code>{format_dh(order['amount'])}</code>"
        )
    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:edit_order_status")
async def edit_order_status_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    await state.set_state(AdminFlow.edit_order_status)
    await callback.message.answer(
        "<b>🛠 تعديل حالة طلب</b>\n"
        "أرسل بالشكل التالي:\n"
        "<code>رقم_الطلب,status</code>\n"
        "مثال: <code>12345678,completed</code>"
    )
    await callback.answer()


@router.message(AdminFlow.edit_order_status)
async def edit_order_status_submit(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    text = (message.text or "").strip()
    if "," not in text:
        await message.answer("⚠️ التنسيق غير صحيح. استخدم: رقم_الطلب,status")
        return
    order_ref_text, status = [part.strip() for part in text.split(",", maxsplit=1)]
    order_id = get_order_id_by_provider_ref(order_ref_text)
    if order_id is None:
        await message.answer("⚠️ لم يُعثر على طلب بهذا الرقم.")
        return
    if not status:
        await message.answer("⚠️ الحالة لا يمكن أن تكون فارغة.")
        return
    ok = set_order_status_by_admin(order_id, status)
    await state.clear()
    if not ok:
        await message.answer(
            "⚠️ تعذر تعديل حالة الطلب. "
            "قد تكون الحالة نهائية (failed/canceled/refunded) أو الانتقال غير مسموح. "
            "عند failed/canceled يُسترد المتبقي تلقائياً.",
            reply_markup=build_admin_menu(),
        )
        return
    await message.answer(
        f"<b>✅ تم تحديث الطلب #{escape(order_ref_text)}</b>\nالحالة الجديدة: <b>{status}</b>",
        reply_markup=build_admin_menu(),
    )


@router.callback_query(F.data == "admin:assign_partner")
async def assign_partner_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    await state.set_state(AdminFlow.assign_partner)
    await callback.message.answer(
        "<b>🎖️ تعيين شريك</b>\n"
        "أرسل <code>معرف المستخدم (User ID)</code> رقمياً فقط، مثال:\n"
        "<code>6238897757</code>"
    )
    await callback.answer()


@router.message(AdminFlow.assign_partner)
async def assign_partner_submit(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ يرجى إرسال معرف رقمي صالح فقط.")
        return
    target_id = int(raw)
    add_user(target_id)
    ok = set_user_referral_level(target_id, 4)
    await state.clear()
    if not ok:
        await message.answer(
            "⚠️ تعذر تعيين الشريك. تحقق من المعرف وحاول مجدداً.",
            reply_markup=build_admin_menu(),
        )
        return
    try:
        await send_smart_notification(bot, target_id, PARTNER_WELCOME_HTML)
    except Exception as exc:
        logger.warning("Partner welcome notify failed for %s: %s", target_id, exc)
    await message.answer(
        f"<b>✅ تم تعيين المستخدم</b> <code>{target_id}</code> <b>كشريك</b> "
        f"(عمولة <b>25%</b>).",
        reply_markup=build_admin_menu(),
    )


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    await state.set_state(AdminFlow.send_broadcast)
    await callback.message.answer("<b>📢 أرسل نص الإعلان الآن</b>")
    await callback.answer()


@router.message(AdminFlow.send_broadcast)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ الإعلان فارغ.")
        return
    success = 0
    failed = 0
    for user_id in get_all_user_ids():
        try:
            await send_smart_notification(
                bot,
                user_id,
                f"<b>📢 إعلان من الإدارة</b>\n\n{text}",
            )
            success += 1
        except Exception:
            failed += 1
    await state.clear()
    await message.answer(
        f"<b>✅ تم إرسال الإعلان</b>\nنجاح: <b>{success}</b>\nفشل: <b>{failed}</b>",
        reply_markup=build_admin_menu(),
    )


def _format_admin_withdrawal_card(
    withdrawal: WithdrawalRecord,
    *,
    position: int,
    total: int,
) -> str:
    user = get_user(withdrawal["user_id"])
    telegram_name = str((user or {}).get("telegram_name") or "").strip()
    withdraw_amount = float(withdrawal["amount"])
    details = safe_withdraw_details(withdrawal.get("details_json"))
    detail_lines = format_withdraw_details_admin_lines(details, amount_dh=withdraw_amount)
    method = escape(str(withdrawal["method"]))
    withdrawal_type = str(withdrawal.get("withdrawal_type") or "normal")
    title = "🎁 سحب أرباح إحالة" if withdrawal_type == "referral" else "💸 سحب عادي"
    lines = [
        "<b>💸 معالجة طلبات السحب</b>",
        f"<i>({position} من {total})</i>",
        "",
        f"<b>{title}</b> <code>#{withdrawal['id']}</code>",
        "<b>معلومات حساب الزبون</b>",
    ]
    if telegram_name:
        lines.append(f"• اسم تيليجرام: <code>{escape(telegram_name)}</code>")
    else:
        lines.append("• اسم تيليجرام: —")
    lines.append(f"• معرف المستخدم: <code>{withdrawal['user_id']}</code>")
    if detail_lines:
        lines.extend(detail_lines)
    else:
        lines.append("• معلومات السحب: —")
    lines.extend(["", f"• المبلغ المطلوب سحبه: <b>{format_dh(withdraw_amount)}</b>", f"• طريقة السحب: <b>{method}</b>"])
    if withdrawal_type == "referral":
        referral_balance = float((user or {}).get("referral_balance") or 0.0)
        lines.extend(
            [
                "• نوع الطلب: <b>سحب أرباح إحالة</b>",
                f"• رصيد الإحالة المتبقي بعد الحجز: <b>{format_dh(referral_balance)}</b>",
            ]
        )
    else:
        ledger = get_method_withdrawal_ledger_summary(
            withdrawal["user_id"],
            str(withdrawal["method"]),
        )
        deposited_total = float(ledger["deposited"])
        withdrawn_total = float(ledger["completed_withdrawn"])
        pending_total = float(ledger["pending_withdrawn"])
        available_before_approval = float(ledger["available"]) + withdraw_amount
        available_after_approval = max(0.0, float(ledger["available"]))
        lines.extend(
            [
                "• نوع الطلب: <b>سحب عادي</b>",
                f"• إجمالي الإيداع عبر نفس الطريقة: <b>{format_dh(deposited_total)}</b>",
                f"• سحوبات مكتملة من نفس الطريقة: <b>{format_dh(withdrawn_total)}</b>",
                f"• سحوبات معلّقة أخرى (بما فيها هذا): <b>{format_dh(pending_total)}</b>",
                f"• المتبقي القابل للسحب (قبل قبول هذا الطلب): <b>{format_dh(available_before_approval)}</b>",
                f"• المتبقي بعد قبول هذا الطلب: <b>{format_dh(available_after_approval)}</b>",
            ]
        )
        if withdraw_amount > available_before_approval + 0.000001:
            lines.extend(
                [
                    "",
                    "⚠️ <b>تنبيه:</b> مبلغ السحب أكبر من المتبقي المحسوب لهذه الطريقة.",
                ]
            )
    lines.append(f"• تاريخ الطلب: <code>{escape(str(withdrawal['created_at']))}</code>")
    return "\n".join(lines)


async def _render_admin_withdrawal_queue(
    message: Message,
    *,
    notice: str | None = None,
) -> None:
    pending = get_pending_withdrawals_ordered()
    if not pending:
        text = "<b>💸 معالجة طلبات السحب</b>\n\nلا توجد طلبات سحب معلّقة حالياً."
        if notice:
            text = f"{notice}\n\n{text}"
        await message.edit_text(text, parse_mode="HTML", reply_markup=build_admin_menu())
        return

    withdrawal = pending[0]
    card = _format_admin_withdrawal_card(withdrawal, position=1, total=len(pending))
    if notice:
        card = f"{notice}\n\n{card}"
    await message.edit_text(
        card,
        parse_mode="HTML",
        reply_markup=build_admin_withdrawal_actions(withdrawal["id"]),
    )


def _customer_contact_html(user_id: int, telegram_name: str | None) -> str:
    display = str(telegram_name or "").strip()
    if not display:
        display = f"مستخدم {user_id}"
    return f'<a href="tg://user?id={user_id}">{escape(display)}</a>'


def _format_admin_manual_order_card(
    order: OrderRecord,
    *,
    position: int,
    total: int,
) -> str:
    user = get_user(order["user_id"])
    telegram_name = str((user or {}).get("telegram_name") or "").strip()
    lines = [
        "<b>📋 طلبات بانتظار الأدمن</b>",
        f"<i>({position} من {total})</i>",
        "",
        f"<b>طلب يدوي</b> <code>#{escape(user_visible_order_ref(order))}</code>",
        f"• الخدمة: <b>{escape(str(order['service_name']))}</b>",
        f"• العميل: {_customer_contact_html(order['user_id'], telegram_name or None)}",
        f"• المعرف: <code>{order['user_id']}</code>",
        f"• الرابط / البيانات: <code>{escape(str(order['link']))}</code>",
        f"• الكمية: <code>{order['quantity']}</code>",
        f"• المبلغ: <b>{format_dh(order['amount'])}</b>",
        f"• تاريخ الطلب: <code>{escape(str(order.get('created_at') or '—'))}</code>",
    ]
    return "\n".join(lines)


async def _render_admin_manual_order_queue(
    message: Message,
    *,
    notice: str | None = None,
) -> None:
    pending = get_pending_admin_orders_ordered()
    if not pending:
        text = "<b>📋 طلبات بانتظار الأدمن</b>\n\nلا توجد طلبات معلّقة حالياً."
        if notice:
            text = f"{notice}\n\n{text}"
        await message.edit_text(text, parse_mode="HTML", reply_markup=build_admin_menu())
        return

    order = pending[0]
    card = _format_admin_manual_order_card(order, position=1, total=len(pending))
    if notice:
        card = f"{notice}\n\n{card}"
    await message.edit_text(
        card,
        parse_mode="HTML",
        reply_markup=build_admin_manual_order_actions(order["id"]),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "admin:manual_orders")
async def admin_manual_orders_start_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    pending = get_pending_admin_orders_ordered()
    if not pending:
        await callback.message.answer(
            "<b>📋 طلبات بانتظار الأدمن</b>\n\nلا توجد طلبات معلّقة حالياً.",
            parse_mode="HTML",
            reply_markup=build_admin_menu(),
        )
        await callback.answer()
        return
    order = pending[0]
    await callback.message.answer(
        _format_admin_manual_order_card(order, position=1, total=len(pending)),
        parse_mode="HTML",
        reply_markup=build_admin_manual_order_actions(order["id"]),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:manual:complete:"))
async def admin_manual_order_complete_handler(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    try:
        order_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("طلب غير صالح", show_alert=True)
        return

    pending_by_id = {order["id"]: order for order in get_pending_admin_orders_ordered()}
    order = pending_by_id.get(order_id)
    if order is None:
        await callback.answer("تمت معالجة هذا الطلب سابقاً", show_alert=True)
        await _render_admin_manual_order_queue(callback.message)
        return

    user_id = int(order["user_id"])
    ok = set_order_status_by_admin(order_id, "completed")
    if not ok:
        await callback.answer("تعذر إتمام الطلب", show_alert=True)
        return
    notify_warning: str | None = None
    if user_id:
        order_ref = escape(user_visible_order_ref(order))
        try:
            await send_smart_notification(
                bot,
                user_id,
                (
                    "<b>✅ SOLDIUM | تم تنفيذ طلبك</b>\n"
                    f"تم تنفيذ الطلب <code>#{order_ref}</code> بنجاح.\n"
                    "شكراً لثقتك بنا!"
                ),
            )
        except Exception as exc:
            logger.warning(
                "Manual order completion notification failed for user %s, order %s: %s",
                user_id,
                order_id,
                exc,
            )
            notify_warning = "⚠️ تعذر إرسال إشعار للمستخدم."
    notice = f"<b>✅ تم تنفيذ الطلب</b> <code>#{order_ref}</code>."
    if notify_warning:
        notice = f"{notice}\n{notify_warning}"
    await _render_admin_manual_order_queue(callback.message, notice=notice)
    await callback.answer("تم تنفيذ الطلب")


@router.callback_query(F.data.startswith("admin:manual:reject:"))
async def admin_manual_order_reject_handler(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    try:
        order_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("طلب غير صالح", show_alert=True)
        return

    pending = get_pending_admin_orders_ordered()
    pending_by_id = {order["id"]: order for order in pending}
    order = pending_by_id.get(order_id)
    if order is None:
        await callback.answer("تمت معالجة هذا الطلب سابقاً", show_alert=True)
        await _render_admin_manual_order_queue(callback.message)
        return

    ok = set_order_status_by_admin(order_id, "canceled")
    if not ok:
        await callback.answer("تعذر رفض الطلب", show_alert=True)
        return

    notify_warning: str | None = None
    order_ref = escape(user_visible_order_ref(order))
    try:
        await send_smart_notification(
            bot,
            order["user_id"],
            (
                "<b>⚠️ SOLDIUM | تم رفض الطلب</b>\n"
                f"تعذّر تنفيذ الطلب <code>#{order_ref}</code>.\n"
                f"تم إرجاع <b>{format_dh(order['amount'])}</b> إلى رصيدك.\n"
                "تواصل مع الدعم إذا احتجت مساعدة."
            ),
        )
    except Exception as exc:
        logger.warning(
            "Manual order rejection notification failed for user %s, order %s: %s",
            order["user_id"],
            order_id,
            exc,
        )
        notify_warning = "⚠️ تعذر إرسال إشعار للمستخدم."
    notice = (
        f"<b>❌ تم رفض الطلب</b> <code>#{order_ref}</code> "
        f"وإرجاع <b>{format_dh(order['amount'])}</b> للمستخدم <code>{order['user_id']}</code>."
    )
    if notify_warning:
        notice = f"{notice}\n{notify_warning}"
    await _render_admin_manual_order_queue(callback.message, notice=notice)
    await callback.answer("تم رفض الطلب وإرجاع الرصيد", show_alert=True)


@router.callback_query(F.data == "admin:withdrawals")
async def admin_withdrawals_start_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    pending = get_pending_withdrawals_ordered()
    if not pending:
        await callback.message.answer(
            "<b>💸 معالجة طلبات السحب</b>\n\nلا توجد طلبات سحب معلّقة حالياً.",
            parse_mode="HTML",
            reply_markup=build_admin_menu(),
        )
        await callback.answer()
        return
    withdrawal = pending[0]
    await callback.message.answer(
        _format_admin_withdrawal_card(withdrawal, position=1, total=len(pending)),
        parse_mode="HTML",
        reply_markup=build_admin_withdrawal_actions(withdrawal["id"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:withdraw:approve:"))
async def admin_withdraw_approve_handler(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    try:
        withdrawal_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("طلب غير صالح", show_alert=True)
        return

    existing = get_withdrawal(withdrawal_id)
    if existing is None or existing["status"] != "pending":
        await callback.answer("تمت معالجة هذا الطلب سابقاً", show_alert=True)
        await _render_admin_withdrawal_queue(callback.message)
        return

    approved = approve_withdrawal_by_admin(withdrawal_id)
    if approved is None:
        await callback.answer("تعذر اعتماد الطلب", show_alert=True)
        return

    is_referral = str(approved.get("withdrawal_type") or "normal") == "referral"
    notification_title = (
        "✨ SOLDIUM | تم تحويل سحب أرباح الإحالة"
        if is_referral
        else "✨ SOLDIUM | تم تحويل مبلغ السحب"
    )
    notify_warning: str | None = None
    try:
        await send_smart_notification(
            bot,
            approved["user_id"],
            (
                f"<b>{notification_title}</b>\n"
                f"تم تحويل <b>{format_dh(approved['amount'])}</b> إلى حسابك عبر "
                f"<b>{escape(str(approved['method']))}</b>.\n"
                "شكراً لثقتك بنا!"
            ),
        )
    except Exception as exc:
        logger.warning(
            "Withdraw approval notification failed for user %s, withdrawal %s: %s",
            approved["user_id"],
            withdrawal_id,
            exc,
        )
        notify_warning = "⚠️ تعذر إرسال إشعار للمستخدم."
    notice = (
        f"<b>✅ تمت معالجة {'سحب الإحالة' if is_referral else 'طلب السحب'}</b> <code>#{withdrawal_id}</code> "
        f"للمستخدم <code>{approved['user_id']}</code>."
    )
    if notify_warning:
        notice = f"{notice}\n{notify_warning}"
    await _render_admin_withdrawal_queue(callback.message, notice=notice)
    await callback.answer("تم اعتماد السحب")


@router.callback_query(F.data.startswith("admin:withdraw:reject:"))
async def admin_withdraw_reject_handler(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    try:
        withdrawal_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("طلب غير صالح", show_alert=True)
        return

    existing = get_withdrawal(withdrawal_id)
    if existing is None or existing["status"] != "pending":
        await callback.answer("تمت معالجة هذا الطلب سابقاً", show_alert=True)
        await _render_admin_withdrawal_queue(callback.message)
        return

    rejected = reject_withdrawal_by_admin(withdrawal_id)
    if rejected is None:
        await callback.answer("تعذر رفض الطلب", show_alert=True)
        return

    is_referral = str(rejected.get("withdrawal_type") or "normal") == "referral"
    refund_target = "أرباح الإحالة" if is_referral else "رصيدك"
    notify_warning: str | None = None
    try:
        await send_smart_notification(
            bot,
            rejected["user_id"],
            (
                "<b>⚠️ SOLDIUM | تم رفض طلب السحب</b>\n"
                f"تعذّر معالجة طلب سحب بمبلغ <b>{format_dh(rejected['amount'])}</b>.\n"
                f"تم إرجاع المبلغ إلى {refund_target}.\n"
                "يرجى التواصل مع الدعم إذا كنت بحاجة إلى مساعدة."
            ),
        )
    except Exception as exc:
        logger.warning(
            "Withdraw rejection notification failed for user %s, withdrawal %s: %s",
            rejected["user_id"],
            withdrawal_id,
            exc,
        )
        notify_warning = "⚠️ تعذر إرسال إشعار للمستخدم."
    notice = (
        f"<b>❌ تم رفض طلب السحب</b> <code>#{withdrawal_id}</code> "
        f"وإرجاع <b>{format_dh(rejected['amount'])}</b> إلى {refund_target} للمستخدم "
        f"<code>{rejected['user_id']}</code>."
    )
    if notify_warning:
        notice = f"{notice}\n{notify_warning}"
    await _render_admin_withdrawal_queue(callback.message, notice=notice)
    await callback.answer("تم رفض الطلب وإرجاع الرصيد", show_alert=True)
