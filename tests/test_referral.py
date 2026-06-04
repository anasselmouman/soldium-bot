from services.referral import (
    PARTNER_LEVEL_UPGRADE_HTML,
    REFERRAL_TIERS,
    compute_commission,
    commission_rate,
    format_level_upgrade_notification,
    meets_next_level_requirements,
    net_spent_amount,
    parse_referrer_id_from_start_payload,
    referral_level_name,
)





def test_parse_referrer_payload_variants():

    assert parse_referrer_id_from_start_payload(None) is None

    assert parse_referrer_id_from_start_payload("") is None

    assert parse_referrer_id_from_start_payload("6238897757") == 6238897757

    assert parse_referrer_id_from_start_payload("ref_6238897757") == 6238897757

    assert parse_referrer_id_from_start_payload("REF6238897757") == 6238897757





def test_net_spent_and_commission_by_level():

    assert net_spent_amount(100.0, 30.0) == 70.0

    assert net_spent_amount(100.0, 130.0) == 0.0

    assert compute_commission(100.0, 1) == 10.0

    assert compute_commission(100.0, 2) == 15.0

    assert compute_commission(100.0, 3) == 20.0

    assert compute_commission(100.0, 4) == 25.0

    assert commission_rate(4) == 0.25





def test_tier_names_and_upgrade_requirements():

    assert referral_level_name(1) == "ناشر"

    assert REFERRAL_TIERS[2]["active_users"] == 10

    assert meets_next_level_requirements(1, active_users=10, earnings=150.0) is True

    assert meets_next_level_requirements(1, active_users=9, earnings=200.0) is False

    assert meets_next_level_requirements(4, active_users=100, earnings=9999.0) is False


def test_level_upgrade_notification_messages():
    msg_l2 = format_level_upgrade_notification(2)
    assert "محترف" in msg_l2
    assert "15%" in msg_l2
    msg_l3 = format_level_upgrade_notification(3)
    assert "خبير" in msg_l3
    assert "20%" in msg_l3
    assert format_level_upgrade_notification(4) == PARTNER_LEVEL_UPGRADE_HTML

