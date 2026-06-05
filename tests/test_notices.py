"""اختبارات الملاحظات الموحّدة وإرشادات الرابط."""

from utils.notices import (
    GLOBAL_SERVICE_NOTES_HTML,
    IPTV_PANEL_NOTES_HTML,
    IPTV_PANEL_SERVICE_NOTICE,
    IPTV_WC2026_NOTES_HTML,
    resolve_link_prompt,
    resolve_section_notice,
    resolve_service_notice,
)


def test_resolve_service_notice_returns_global_notes():
    service = {"name": "Any", "note": "ignored legacy note", "notice_key": "x_spaces"}
    assert resolve_service_notice(service) == GLOBAL_SERVICE_NOTES_HTML
    assert "الحساب عام" in resolve_service_notice(service)


def test_resolve_section_notice_returns_global_notes():
    section = {"section_notice_key": "x_live_broadcast", "description": "ignored"}
    assert resolve_section_notice(section) == GLOBAL_SERVICE_NOTES_HTML


def test_resolve_link_prompt_x_spaces():
    service = {"link_prompt_key": "x_spaces"}
    text, allow_username, allow_free_text = resolve_link_prompt("x", "spaces", service=service)
    assert text is not None
    assert "spaces" in text.lower()
    assert allow_username is False
    assert allow_free_text is False


def test_resolve_link_prompt_telegram_future_posts():
    text, allow_username, allow_free_text = resolve_link_prompt("telegram", None, "future_posts")
    assert text is not None
    assert "المنشورات القادمة" in text
    assert allow_username is False
    assert allow_free_text is False


def test_resolve_iptv_wc2026_notices():
    section = {"section_notice_key": "iptv_wc2026"}
    assert resolve_section_notice(section, platform_key="subscriptions") == IPTV_WC2026_NOTES_HTML
    service = {"section_key": "iptv_wc2026"}
    assert resolve_service_notice(service) == IPTV_WC2026_NOTES_HTML


def test_resolve_link_prompt_iptv_wc2026_allows_free_text():
    text, allow_username, allow_free_text = resolve_link_prompt(
        "subscriptions", "iptv_wc2026", service={"section_key": "iptv_wc2026"}
    )
    assert "اسمك" in text
    assert allow_username is False
    assert allow_free_text is True


def test_resolve_iptv_panel_section_notice_only():
    section = {"section_notice_key": "iptv_panel"}
    assert resolve_section_notice(section, platform_key="subscriptions") == IPTV_PANEL_NOTES_HTML
    assert "Gozibra" in IPTV_PANEL_NOTES_HTML
    service = {"section_key": "iptv_panel"}
    assert resolve_service_notice(service) == IPTV_PANEL_SERVICE_NOTICE


def test_resolve_link_prompt_iptv_panel_allows_free_text():
    text, allow_username, allow_free_text = resolve_link_prompt(
        "subscriptions", "iptv_panel", service={"section_key": "iptv_panel"}
    )
    assert allow_free_text is True
