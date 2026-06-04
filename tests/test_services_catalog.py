from services_config import SERVICES
from utils.services import compute_effective_limits, find_service_location


def _collect_service_ids():
    ids: list[str] = []
    for item, *_ in _iter_entries():
        ids.append(str(item["id"]))
    return ids


def _iter_entries():
    for platform_key, category in SERVICES.items():
        if "items" in category:
            for item in category["items"]:
                yield item, platform_key
        for item in category.get("direct_items", []):
            yield item, platform_key
        for section_key, section in (category.get("sections") or {}).items():
            for item in section.get("items", []):
                yield item, platform_key, section_key
            for subsection_key, subsection in (section.get("subsections") or {}).items():
                for item in subsection.get("items", []):
                    yield item, platform_key, section_key, subsection_key


def test_catalog_service_ids_are_unique():
    ids = _collect_service_ids()
    assert len(ids) == len(set(ids))
    assert len(ids) >= 200


def test_find_service_location_instagram_followers():
    located = find_service_location("4823")
    assert located is not None
    service, platform, section, subsection = located
    assert str(service["id"]) == "4823"
    assert platform == "instagram"
    assert section == "followers"
    assert subsection is None


def test_compute_effective_limits_intersection():
    mn, mx = compute_effective_limits(100, 10_000, (50, 500_000), quantity_cap=1_000_000)
    assert mn == 100
    assert mx == 10_000

    mn2, mx2 = compute_effective_limits(10, 1_000_000_000, (100, 500), quantity_cap=10_000_000)
    assert mn2 == 100
    assert mx2 == 500


def test_compute_effective_limits_without_provider():
    mn, mx = compute_effective_limits(10, 50_000, None, quantity_cap=10_000_000)
    assert mn == 10
    assert mx == 50_000


def test_find_service_location_tiktok_comment_has_link_type():
    located = find_service_location("4371")
    assert located is not None
    service, platform, section, _ = located
    assert service.get("link_type") == "comment"
    assert platform == "tiktok"
