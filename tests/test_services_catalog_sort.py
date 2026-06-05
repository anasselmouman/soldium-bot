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
    assert section_keys[:4] == ["followers", "views", "likes", "interaction"]
