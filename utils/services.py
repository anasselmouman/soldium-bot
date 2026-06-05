from services_config import SERVICES
from utils.money import to_decimal

ServiceLocation = tuple[dict, str, str | None, str | None]


def get_category_title(category_key: str) -> str:
    category = SERVICES.get(category_key)
    if not category:
        return "غير معروف"
    return str(category["title"])


def _iter_catalog_entries():
    for platform_key, category in SERVICES.items():
        if "items" in category:
            for item in category["items"]:
                yield item, platform_key, None, None
        for item in category.get("direct_items", []):
            yield item, platform_key, "direct", None
        for section_key, section in (category.get("sections") or {}).items():
            for item in section.get("items", []):
                yield item, platform_key, section_key, None
            for subsection_key, subsection in (section.get("subsections") or {}).items():
                for item in subsection.get("items", []):
                    yield item, platform_key, section_key, subsection_key


def find_service_by_id(service_id: str) -> dict | None:
    located = find_service_location(service_id)
    return located[0] if located else None


def find_service_location(service_id: str) -> ServiceLocation | None:
    sid = str(service_id).strip()
    if not sid:
        return None
    for item, platform_key, section_key, subsection_key in _iter_catalog_entries():
        if str(item.get("id", "")) == sid:
            return item, platform_key, section_key, subsection_key
    return None


def compute_effective_limits(
    local_min: int,
    local_max: int,
    provider_limits: tuple[int, int] | None,
    *,
    quantity_cap: int | None = None,
) -> tuple[int, int]:
    """تقاطع حدود الكتالوج مع حدود المزود وحد الأمان العام."""
    capped_max = local_max
    if quantity_cap is not None and quantity_cap > 0:
        capped_max = min(capped_max, quantity_cap)
    if provider_limits is None:
        return local_min, max(local_min, capped_max)
    provider_min, provider_max = provider_limits
    effective_min = max(local_min, provider_min)
    effective_max = min(capped_max, provider_max)
    if effective_max < effective_min:
        effective_max = effective_min
    return effective_min, effective_max


def order_total_price_dh(service: dict, quantity: int):
    """التكلفة بالدرهم: لكل وحدة مباشرة، أو السعر لكل 1000 × الكمية / 1000."""
    if service.get("price_per_unit"):
        return to_decimal(service["price"]) * to_decimal(quantity)
    total = (to_decimal(service["price"]) * to_decimal(quantity)) / to_decimal(1000)
    return to_decimal(total)
