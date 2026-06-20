from utils.order_flow import (
    build_invoice_text,
    build_link_step_prompt,
    build_service_intro_text,
    build_service_summary_text,
    build_service_summary_text_short,
    format_service_note_html,
    parse_subsection_callback,
    validate_platform_link,
)


def test_parse_subsection_callback_valid():
    parsed = parse_subsection_callback("order:subsection:telegram:post_interactions:premium_interactions")
    assert parsed == ("telegram", "post_interactions", "premium_interactions")
    parsed_short = parse_subsection_callback("o:ss:telegram:post_interactions:premium_interactions")
    assert parsed_short == ("telegram", "post_interactions", "premium_interactions")


def test_parse_subsection_callback_invalid():
    assert parse_subsection_callback("os:telegram:post_interactions:premium_interactions") is None
    assert parse_subsection_callback("order:subsection:telegram") is None


def test_validate_platform_link_requires_platform_keyword():
    ok, error = validate_platform_link("https://instagram.com/p/abc", "instagram")
    assert ok is True
    assert error == ""

    ok, error = validate_platform_link("https://youtube.com/watch?v=1", "instagram")
    assert ok is False
    assert "إنستغرام" in error


def test_validate_instagram_accepts_profile_or_post():
    assert validate_platform_link("https://www.instagram.com/username/", "instagram")[0] is True
    assert validate_platform_link("https://www.instagram.com/reels/abc123/", "instagram")[0] is True
    ok, error = validate_platform_link("instagram.com/user", "instagram")
    assert ok is False
    assert "http" in error


def test_validate_youtube_accepts_any_youtube_link():
    assert validate_platform_link("https://youtu.be/AbCDef123", "youtube")[0] is True
    assert validate_platform_link("https://www.youtube.com/watch?v=AbCDef123", "youtube")[0] is True
    assert validate_platform_link("https://www.youtube.com/@creator", "youtube")[0] is True


def test_validate_tiktok_accepts_profile_or_video():
    assert validate_platform_link("https://www.tiktok.com/@user/video/1234567890123", "tiktok")[0] is True
    assert validate_platform_link("https://www.tiktok.com/@user", "tiktok")[0] is True
    assert validate_platform_link("https://vt.tiktok.com/ZMxxxx/", "tiktok")[0] is True


def test_validate_telegram_accepts_channel_post_or_username():
    assert validate_platform_link("https://t.me/mychannel/123", "telegram")[0] is True
    assert validate_platform_link("https://t.me/mychannel", "telegram")[0] is True
    assert validate_platform_link("@mychannel", "telegram", allow_username=True)[0] is True
    assert validate_platform_link("https://t.me/ExampleBot", "telegram")[0] is True

    ok, error = validate_platform_link("https://instagram.com/p/x", "telegram")
    assert ok is False
    assert "تيليجرام" in error


def test_validate_facebook_keywords():
    assert validate_platform_link("https://facebook.com/page", "facebook")[0] is True
    assert validate_platform_link("https://fb.watch/abc", "facebook")[0] is True


def test_validate_x_accepts_profile_tweet_or_username():
    assert validate_platform_link(
        "https://x.com/someuser/status/1234567890123456789",
        "x",
        section_key="likes",
    )[0] is True
    assert validate_platform_link("https://x.com/someuser", "x", section_key="followers")[0] is True
    assert validate_platform_link("@someuser", "x", section_key="followers", allow_username=True)[0] is True
    assert validate_platform_link("https://x.com/i/spaces/1vOGwADXZjBQj", "x", section_key="spaces")[0] is True
    assert validate_platform_link(
        "https://x.com/i/broadcasts/1lPKqABCDEF",
        "x",
        section_key="live_broadcast",
    )[0] is True

    ok, error = validate_platform_link("https://instagram.com/p/x", "x")
    assert ok is False
    assert "X" in error or "تويتر" in error


def test_validate_x_direct_messages_multiline_first_line():
    ok, _ = validate_platform_link(
        "https://x.com/targetuser\nelonmusk, dogecoin, Nike, tesla, apple",
        "x",
        section_key="direct_messages",
    )
    assert ok is True


def test_validate_x_direct_messages_rejects_too_few_usernames():
    ok, error = validate_platform_link(
        "https://x.com/targetuser\nelonmusk, dogecoin",
        "x",
        section_key="direct_messages",
    )
    assert ok is False
    assert "5" in error


def test_validate_x_mentions_requires_status_path():
    ok, error = validate_platform_link(
        "https://x.com/someuser",
        "x",
        section_key="mentions",
    )
    assert ok is False
    assert "/status/" in error


def test_validate_x_live_broadcast_rejects_normal_tweet():
    ok, error = validate_platform_link(
        "https://x.com/olive/status/1234567890",
        "x",
        section_key="live_broadcast",
    )
    assert ok is False
    assert "broadcast" in error.lower() or "بث" in error


def test_validate_x_live_broadcast_rejects_broadcast_in_username_path():
    ok, _ = validate_platform_link(
        "https://x.com/foo/broadcast/bar",
        "x",
        section_key="live_broadcast",
    )
    assert ok is False


def test_validate_x_likes_rejects_username_without_allow():
    ok, error = validate_platform_link(
        "@someuser",
        "x",
        section_key="likes",
        allow_username=False,
    )
    assert ok is False
    assert "http" in error


def test_validate_telegram_future_posts_requires_post_link():
    ok, _ = validate_platform_link(
        "https://t.me/mychannel/123",
        "telegram",
        section_key="post_views",
        subsection_key="future_posts",
    )
    assert ok is True
    ok2, error2 = validate_platform_link(
        "https://t.me/mychannel",
        "telegram",
        section_key="post_views",
        subsection_key="future_posts",
    )
    assert ok2 is False
    assert "منشور" in error2


def test_validate_telegram_past_posts_requires_channel_link():
    ok, _ = validate_platform_link(
        "https://t.me/mychannel",
        "telegram",
        section_key="post_views",
        subsection_key="past_posts",
        allow_username=True,
    )
    assert ok is True
    ok2, error2 = validate_platform_link(
        "https://t.me/mychannel/123",
        "telegram",
        section_key="post_views",
        subsection_key="past_posts",
    )
    assert ok2 is False
    assert "قناة" in error2


def test_validate_tiktok_comment_link_type():
    service = {"id": "4371", "link_type": "comment"}
    ok, _ = validate_platform_link(
        "https://www.tiktok.com/@user/video/123?comment_id=456",
        "tiktok",
        section_key="likes",
        service=service,
    )
    assert ok is True
    ok2, error2 = validate_platform_link(
        "https://www.tiktok.com/@user/video/123",
        "tiktok",
        section_key="likes",
        service=service,
    )
    assert ok2 is False


def test_format_service_note_html_plain_and_markdown():
    assert "<b>تنبيه</b>" in format_service_note_html("**تنبيه**")
    html_note = "⚠️ <b>هام</b>"
    assert format_service_note_html(html_note) == html_note


def test_build_summary_text_contains_limits_without_link_cta():
    service = {"name": "Test Service", "price": 3.5}
    text = build_service_summary_text(service, "DH", 100, 10000)
    assert "Test Service" in text
    assert "تفاصيل الخدمة التي اخترتها" in text
    assert "الحد الأدنى للطلب" in text
    assert "الحد الأقصى للطلب" in text
    assert "10000" in text
    assert "3.5" in text
    assert "اتبع التعليمات في الرسالة أدناه" in text
    assert "أرسل الرابط كاملاً" not in text


def test_build_summary_text_uses_global_service_notes():
    service = {"name": "Space Service", "price": 1.2, "notice_key": "x_spaces"}
    text = build_service_summary_text(service, "DH", 10, 1000)
    assert "ملاحظات" in text
    assert "الحساب عام" in text
    assert "رابط السبيس" not in text


def test_build_summary_text_short_shows_full_bullet_notes():
    service = {"name": "Test Service", "price": 2.0}
    text = build_service_summary_text_short(service, "DH", 10, 1000)
    assert "<i>" not in text
    assert "…" not in text
    assert "⚠️" not in text
    assert "الحساب عام" in text
    assert "طلب ثانٍ" in text
    assert "- " in text
    assert "<b>اتبع التعليمات في الرسالة أدناه.</b>" in text


def test_build_link_step_prompt_requests_link():
    text = build_link_step_prompt()
    assert "الرابط" in text
    assert "أرسل رابط الحساب أو المنشور الذي تريد تنفيذ الطلب عليه:" in text
    assert "http://" not in text
    assert "https://" not in text


def test_build_intro_text_strips_trailing_price_zeros():
    service = {"name": "متابعين", "price": 12.0}
    text = build_service_intro_text(service, "DH", 10, 100000)
    assert "12 DH" in text
    assert "12.00" not in text
    assert "12.0 DH" not in text


def test_build_invoice_text_for_fixed_quantity():
    service = {"name": "Auto Service", "price": 4}
    link = "https://instagram.com/p/abc123"
    text = build_invoice_text(service, "DH", 1000, 4, link, is_fixed_quantity=True)
    assert "Auto Service" in text
    assert "ثابتة تلقائياً" in text
    assert link in text
