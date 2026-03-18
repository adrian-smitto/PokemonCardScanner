from dataclasses import dataclass
from datetime import datetime, timezone
import requests
import config


@dataclass
class PriceChartingResult:
    card_name: str
    loose_price: float | None
    fetched_at: str = ""
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.loose_price is not None


class PriceChartingClient:
    BASE_URL = "https://www.pricecharting.com/api"

    def __init__(self):
        self._session = requests.Session()
        self._api_key = config.PRICECHARTING_API_KEY

    def fetch_price(self, card_name: str, set_name: str, number: str = "") -> PriceChartingResult:
        if not self._api_key:
            return PriceChartingResult(card_name=card_name, loose_price=None)
        query = f"{card_name} {set_name} {number}".strip()
        try:
            resp = self._session.get(
                f"{self.BASE_URL}/products",
                params={"q": query, "id": "pokemon", "api_key": self._api_key},
                timeout=config.API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            products = resp.json().get("products", [])
            if not products:
                return PriceChartingResult(
                    card_name=card_name, loose_price=None,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )

            product_id = products[0]["id"]
            price_resp = self._session.get(
                f"{self.BASE_URL}/product",
                params={"id": product_id, "api_key": self._api_key},
                timeout=config.API_TIMEOUT_SECONDS,
            )
            price_resp.raise_for_status()
            data = price_resp.json()
            cents = data.get("loose-price")
            loose_price = cents / 100 if cents else None
            return PriceChartingResult(
                card_name=card_name,
                loose_price=loose_price,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            return PriceChartingResult(
                card_name=card_name, loose_price=None,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                error=str(e),
            )
