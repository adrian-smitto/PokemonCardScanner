from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
import requests
import config


VARIANT_ABBREV = {
    "normal":               "normal",
    "holofoil":             "holo",
    "reverseHolofoil":      "rev holo",
    "1stEditionHolofoil":   "1st ed holo",
    "unlimitedHolofoil":    "unltd holo",
}
VARIANT_PRIORITY = list(VARIANT_ABBREV.keys())


@dataclass
class PriceResult:
    card_id: str
    market_price: float | None
    low_price: float | None
    high_price: float | None
    fetched_at: str = ""
    error: str | None = None
    price_variant: str | None = None
    available_variants: list = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.market_price is not None


class PriceClient:
    def __init__(self):
        self._session = requests.Session()
        if config.POKEMONTCG_API_KEY:
            self._session.headers["X-Api-Key"] = config.POKEMONTCG_API_KEY

    def fetch_price(self, card_id: str,
                    target_variant: str | None = None) -> PriceResult:
        url = f"{config.POKEMONTCG_BASE_URL}/cards/{card_id}"
        last_error = None
        for attempt in range(2):  # one retry on failure
            if attempt > 0:
                time.sleep(2)
            try:
                resp = self._session.get(url, timeout=config.API_TIMEOUT_SECONDS)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                market, low, high, used_variant, available = self._extract_prices(
                    data.get("tcgplayer", {}).get("prices", {}),
                    target=target_variant,
                )
                return PriceResult(
                    card_id=card_id,
                    market_price=market,
                    low_price=low,
                    high_price=high,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    price_variant=used_variant,
                    available_variants=available,
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

    def _extract_prices(self, prices: dict,
                        target: str | None = None,
                        ) -> tuple[float | None, float | None, float | None, str | None, list]:
        # Build available list in priority order, then append any unknown variants
        available = [v for v in VARIANT_PRIORITY if v in prices]
        for v in prices:
            if v not in available:
                available.append(v)

        if target:
            if target in prices:
                p = prices[target]
                return p.get("market"), p.get("low"), p.get("high"), target, available
            else:
                return None, None, None, None, available  # variant not found

        # Automatic: use priority order
        for v in VARIANT_PRIORITY:
            if v in prices:
                p = prices[v]
                return p.get("market"), p.get("low"), p.get("high"), v, available

        return None, None, None, None, available
