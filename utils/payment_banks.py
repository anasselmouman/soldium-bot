# -*- coding: utf-8 -*-

"""وسائل الدفع لبوابة شحن الرصيد (بنوك + كاش + كريبتو + بايبال)."""



from __future__ import annotations



import os

from dataclasses import dataclass

from typing import Literal



PaymentKind = Literal["bank", "cash", "crypto", "paypal"]



CRYPTO_LEDGER_NAME = "Binance/Crypto"

PAYPAL_LEDGER_NAME = "PayPal"





def _pay_env(key: str, default: str) -> str:

    """قيمة من البيئة إن وُجدت، وإلا الافتراضي المضمّن في الكود."""

    value = os.environ.get(key, "").strip()

    return value if value else default





@dataclass(frozen=True, slots=True)

class PaymentMethod:

    key: str

    kind: PaymentKind

    button_label: str

    breadcrumb_label: str

    ledger_name: str

    holder_name: str = ""

    account_number: str | None = None

    phone: str | None = None

    paypal_email: str | None = None

    binance_pay_id: str | None = None

    wallet_trc20: str | None = None

    wallet_bep20: str | None = None

    withdraw_button_label: str = ""



    def menu_button_label(self, *, withdraw: bool = False) -> str:
        if withdraw and self.withdraw_button_label:
            return self.withdraw_button_label
        return self.button_label



    @property

    def bank_title(self) -> str:

        """اسم العرض في واجهة التحويل البنكي."""

        return self.ledger_name





PAYMENT_METHODS: tuple[PaymentMethod, ...] = (

    PaymentMethod(

        key="cih",

        kind="bank",

        button_label="🟠 بنك سي آي إتش (CIH Bank) 🔵",

        breadcrumb_label="بنك سي آي إتش",

        ledger_name="بنك سي آي إتش (CIH Bank)",

        holder_name=_pay_env("PAY_CIH_HOLDER", "ANASS EL MOUMAN"),

        account_number=_pay_env("PAY_CIH_ACCOUNT", "5352895211019400"),

    ),

    PaymentMethod(

        key="attijari",

        kind="bank",

        button_label="🟡 التجاري وفا بنك (Attijariwafa Bank) 🟠",

        breadcrumb_label="التجاري وفا بنك",

        ledger_name="التجاري وفا بنك (Attijariwafa Bank)",

        holder_name=_pay_env("PAY_ATTIJARI_HOLDER", "EL MOUMAN ANASS"),

        account_number=_pay_env("PAY_ATTIJARI_ACCOUNT", "0009402300400483"),

    ),

    PaymentMethod(

        key="barid",

        kind="bank",

        button_label="🟡 البريد بنك (Al Barid Bank) 🟡",

        breadcrumb_label="البريد بنك",

        ledger_name="البريد بنك (Al Barid Bank)",

        holder_name=_pay_env("PAY_BARID_HOLDER", "ANASS EL MOUMAN"),

        account_number=_pay_env("PAY_BARID_ACCOUNT", "13969862"),

    ),

    PaymentMethod(

        key="credit_agricole",

        kind="bank",

        button_label="🟢 القرض الفلاحي (Crédit Agricole) 🟢",

        breadcrumb_label="القرض الفلاحي",

        ledger_name="القرض الفلاحي (Crédit Agricole)",

        holder_name=_pay_env("PAY_CA_HOLDER", "ANASS EL MOUMAN"),

        account_number=_pay_env("PAY_CA_ACCOUNT", "0077124466010126"),

    ),

    PaymentMethod(

        key="populaire",

        kind="bank",

        button_label="🟤 البنك الشعبي (Banque Populaire) 🟤",

        breadcrumb_label="البنك الشعبي",

        ledger_name="البنك الشعبي (Banque Populaire)",

        holder_name=_pay_env("PAY_BP_HOLDER", "EL MOUMEN ANASS"),

        account_number=_pay_env("PAY_BP_ACCOUNT", "2111178649390008"),

    ),

    PaymentMethod(

        key="cashplus",

        kind="cash",

        button_label="💸 كاش بلوس (CashPlus) 💸",

        breadcrumb_label="كاش بلوس",

        ledger_name="CashPlus",

        holder_name=_pay_env("PAY_CASHPLUS_HOLDER", "ANASS EL MOUMAN"),

        phone=_pay_env("PAY_CASHPLUS_PHONE", "0656910770"),

    ),

    PaymentMethod(

        key="wafacash",

        kind="cash",

        button_label="🪙 وفاكاش (Wafacash) 🪙",

        breadcrumb_label="وفاكاش",

        ledger_name="Wafacash",

        holder_name=_pay_env("PAY_WAFACASH_HOLDER", "ANASS EL-MOUMAN"),

        phone=_pay_env("PAY_WAFACASH_PHONE", "0656910770"),

    ),

    PaymentMethod(

        key="crypto",

        kind="crypto",

        button_label="💎 العملات الرقمية (Crypto) 💎",

        withdraw_button_label="🔗 العملات الرقمية (Crypto) 🔗",

        breadcrumb_label="USDT / Crypto",

        ledger_name=CRYPTO_LEDGER_NAME,

        binance_pay_id=_pay_env("PAY_BINANCE_ID", "784548487"),

        wallet_trc20=_pay_env("PAY_USDT_TRC20", "TNVUjeJLFdexp6P3V5TQFsmVfvozcsKzQ4"),

        wallet_bep20=_pay_env("PAY_USDT_BEP20", "0xfee61cdf284269bd5733befcac8bb8b85a4e8d99"),

    ),

    PaymentMethod(

        key="paypal",

        kind="paypal",

        button_label="🅿️ بنك بايبال (PayPal) 🅿️",

        breadcrumb_label="PayPal",

        ledger_name=PAYPAL_LEDGER_NAME,

        paypal_email=_pay_env("PAY_PAYPAL_EMAIL", "anasselmomn@gmail.com"),

    ),

)



PAYMENT_BY_KEY: dict[str, PaymentMethod] = {m.key: m for m in PAYMENT_METHODS}

PAYMENT_BY_LEDGER: dict[str, PaymentMethod] = {m.ledger_name: m for m in PAYMENT_METHODS}



# توافق مع الاستيرادات السابقة

BankAccount = PaymentMethod

BANK_ACCOUNTS = tuple(m for m in PAYMENT_METHODS if m.kind == "bank")

BANK_BY_KEY = {m.key: m for m in BANK_ACCOUNTS}





def is_crypto_ledger(method_name: str) -> bool:
    name = method_name.strip()
    if name == CRYPTO_LEDGER_NAME:
        return True
    return name.startswith("USDT —")





def is_paypal_ledger(method_name: str) -> bool:

    return method_name.strip() == PAYPAL_LEDGER_NAME





def is_usd_deposit_ledger(method_name: str) -> bool:

    return is_crypto_ledger(method_name) or is_paypal_ledger(method_name)





def get_payment_method_by_ledger(method_name: str) -> PaymentMethod | None:

    return PAYMENT_BY_LEDGER.get(method_name.strip())

