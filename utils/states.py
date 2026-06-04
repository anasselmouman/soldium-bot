from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    choose_category = State()
    choose_subcategory = State()
    choose_service = State()
    enter_link = State()
    enter_quantity = State()
    confirm_order = State()


class DepositFlow(StatesGroup):
    waiting_for_receipt = State()
    waiting_for_recharge_code = State()


class WithdrawFlow(StatesGroup):
    enter_crypto_address = State()
    enter_amount = State()
    enter_details = State()
    confirm = State()


class ReferralFlow(StatesGroup):
    transfer_amount = State()
    withdraw_crypto_address = State()
    withdraw_amount = State()
    withdraw_details = State()
    withdraw_confirm = State()


class AccountFlow(StatesGroup):
    search_orders = State()


class AdminFlow(StatesGroup):
    send_broadcast = State()
    confirm_deposit_amount = State()
    confirm_recharge_face_value = State()
    edit_order_status = State()
    assign_partner = State()
