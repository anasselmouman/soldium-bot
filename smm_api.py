import json
import logging

import aiohttp

logger = logging.getLogger(__name__)


class ProviderAuthError(RuntimeError):
    """Raised when the SMM provider rejects API credentials (HTTP 401)."""


class SMMManager:
    """مدير الاتصال مع API الخاص بخدمات SMM."""

    def __init__(self, api_key: str, api_url: str) -> None:
        if not str(api_key or "").strip():
            raise ValueError("api_key is required")
        if not str(api_url or "").strip():
            raise ValueError("api_url is required")
        self.api_key = api_key.strip()
        self.api_url = api_url.strip()

    async def _parse_provider_response(
        self,
        response: aiohttp.ClientResponse,
        *,
        action: str | None,
    ) -> dict | list:
        status = response.status
        raw = await response.read()
        data: dict | list | None = None
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None

        if status == 401:
            err = "Invalid API key"
            if isinstance(data, dict) and data.get("error"):
                err = str(data["error"])
            raise ProviderAuthError(err)

        if status >= 400:
            response.raise_for_status()

        if data is None:
            raise ValueError(f"Unexpected empty response from provider (status={status})")

        if isinstance(data, dict) and data.get("error"):
            err = str(data.get("error"))
            logger.warning("SMM API error: %s | payload_action=%s", err, action)
            raise RuntimeError(err)

        return data

    async def _post(self, action: str, *, timeout_seconds: float = 60) -> dict | list:
        payload = {
            "key": self.api_key,
            "action": action,
        }

        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.api_url, data=payload) as response:
                return await self._parse_provider_response(response, action=action)

    async def _post_payload(self, payload: dict, *, timeout_seconds: float = 60) -> dict | list:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.api_url, data=payload) as response:
                return await self._parse_provider_response(
                    response,
                    action=str(payload.get("action") or ""),
                )

    async def get_balance(self) -> dict:
        data = await self._post(action="balance")
        if isinstance(data, dict):
            return data
        raise ValueError("صيغة استجابة الرصيد غير متوقعة من API.")

    async def get_services(self, *, timeout_seconds: float = 120) -> list[dict]:
        data = await self._post(action="services", timeout_seconds=timeout_seconds)
        if isinstance(data, list):
            return data
        raise ValueError("صيغة استجابة الخدمات غير متوقعة من API.")

    async def add_order(self, service: int, link: str, quantity: int) -> dict:
        payload = {
            "key": self.api_key,
            "action": "add",
            "service": service,
            "link": link,
            "quantity": quantity,
        }
        data = await self._post_payload(payload)
        if isinstance(data, dict) and "order" in data:
            return data
        logger.error("Unexpected add_order response: %s", data)
        raise ValueError("صيغة استجابة إنشاء الطلب غير متوقعة من API.")

    async def get_order_status(self, order_id: str) -> dict:
        payload = {
            "key": self.api_key,
            "action": "status",
            "order": order_id,
        }
        data = await self._post_payload(payload)
        if isinstance(data, dict):
            return data
        raise ValueError("صيغة استجابة حالة الطلب غير متوقعة من API.")
