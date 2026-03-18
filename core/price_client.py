from dataclasses import dataclass
from datetime import datetime, timezone
import time
import requests
import config


@dataclass
class PriceResult:
    card_id: str
    market_price: float | None
    low_price: float | None
    high_price: float | None
    fetched_at: str = ""
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.market_price is not None


class PriceClient:
    def __init__(self):
        self._session = requests.Session()
        if config.POKEMONTCG_API_KEY:
            self._session.headers["X-Api-Key"] = config.POKEMONTCG_API_KEY

    def fetch_price(self, card_id: str) -> PriceResult:
        url = f"{config.POKEMONTCG_BASE_URL}/cards/{card_id}"
        last_error = None
        for attempt in range(2):  # one retry on failure
            if attempt > 0:
                time.sleep(2)
            try:
                resp = self._session.get(url, timeout=config.API_TIMEOUT_SECONDS)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                market, low, high = self._extract_prices(data.get("tcgplayer", {}).get("prices", {}))
                return PriceResult(
                    card_id=card_id,
                    market_price=market,
                    low_price=low,
                    high_price=high,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as e:
                last_error = e
        return PriceResult(
            card_id=card_id,
            market_price=None,
            low_price=None,
            high_price=None,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            error=str(last_error),
        )

    def _extract_prices(self, prices: dict) -> tuple[float | None, float | None, float | None]:
        """Try price variants in order, return (market, low, high)."""
        for variant in ("normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil", "unlimitedHolofoil"):
            if variant in prices:
                p = prices[variant]
                return p.get("market"), p.get("low"), p.get("high")
        return None, None, None
