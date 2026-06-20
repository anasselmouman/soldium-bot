from services_catalog_db import build_services_dict_from_db


def test_instagram_followers_sorted_cheapest_first():
    services = build_services_dict_from_db()
    items = services["instagram"]["sections"]["followers"]["items"]
    prices = [float(item["price"]) for item in items]
    assert prices == sorted(prices)
    assert str(items[0]["id"]) == "4823"


def test_instagram_views_cheap_generic_first():
    services = build_services_dict_from_db()
    items = services["instagram"]["sections"]["views"]["items"]
    assert str(items[0]["id"]) == "4205"
    assert float(items[0]["price"]) == 2.0
    assert str(items[1]["id"]) == "4222"
    assert str(items[2]["id"]) == "3732"


def test_instagram_sections_marketing_order():
    services = build_services_dict_from_db()
    section_keys = list(services["instagram"]["sections"].keys())
    assert section_keys[:4] == ["likes", "views", "followers", "interaction"]


def test_facebook_sections_marketing_order():
    services = build_services_dict_from_db()
    section_keys = list(services["facebook"]["sections"].keys())
    assert section_keys[:4] == [
        "reactions",
        "video_reels_views",
        "followers_members",
        "live_stream_views",
    ]


def test_tiktok_sections_marketing_order():
    services = build_services_dict_from_db()
    section_keys = list(services["tiktok"]["sections"].keys())
    assert section_keys[:3] == ["likes", "views", "followers"]


def test_telegram_sections_marketing_order():
    services = build_services_dict_from_db()
    section_keys = list(services["telegram"]["sections"].keys())
    assert section_keys[:5] == [
        "post_interactions",
        "post_views",
        "channel_members",
        "post_share",
        "start_bot",
    ]


def test_telegram_section_titles_have_bookend_emojis():
    services = build_services_dict_from_db()
    sections = services["telegram"]["sections"]
    assert sections["post_interactions"]["title"] == "⚡ تفاعلات المنشورات 👍❤️🔥 ⚡"
    assert sections["post_views"]["title"] == "👁️ مشاهدة منشور 👁️"
    assert sections["channel_members"]["title"] == "👥 أعضاء القنوات والمجموعات 👥"
    assert (
        sections["post_views"]["subsections"]["past_posts"]["title"]
        == "🔙 منشورات سابقة (Auto) 🔙"
    )


def test_telegram_post_interactions_includes_automatic_redirect():
    services = build_services_dict_from_db()
    post = services["telegram"]["sections"]["post_interactions"]
    subs = post.get("subsections") or {}
    assert "automatic_interactions" in subs
    redirect = subs["automatic_interactions"]
    assert redirect.get("redirect_section") == "automatic_interactions"
    assert not redirect.get("items")
    assert redirect["title"] == "🔄 تفاعلات تلقائية للمنشورات القادمة 🔄"
    sub_keys = list(subs.keys())
    assert sub_keys.index("automatic_interactions") == len(sub_keys) - 1


def test_telegram_automatic_interactions_section_has_services():
    services = build_services_dict_from_db()
    auto = services["telegram"]["sections"].get("automatic_interactions")
    assert auto is not None
    total = sum(
        len((sub.get("items") or []))
        for sub in (auto.get("subsections") or {}).values()
    )
    assert total > 0


def test_automatic_interaction_services_have_auto_quantity():
    services = build_services_dict_from_db()
    auto = services["telegram"]["sections"]["automatic_interactions"]
    items = [
        item
        for sub in (auto.get("subsections") or {}).values()
        for item in (sub.get("items") or [])
    ]
    assert items
    assert all(item.get("auto_quantity") == 1000 for item in items)


def test_x_live_broadcast_section_notice_key_from_embedded():
    services = build_services_dict_from_db()
    section = services.get("x", {}).get("sections", {}).get("live_broadcast")
    if section is None:
        return
    assert section.get("section_notice_key") == "x_live_broadcast"


def test_x_spaces_service_has_link_prompt_key():
    services = build_services_dict_from_db()
    spaces = (services.get("x") or {}).get("sections", {}).get("spaces")
    if not spaces:
        return
    items = spaces.get("items") or []
    assert items
    assert any(item.get("link_prompt_key") == "x_spaces" for item in items)
