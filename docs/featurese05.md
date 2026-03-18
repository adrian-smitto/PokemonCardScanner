# Features E05 — Dual Price Sources

Covers stories: E05-US01, E05-US02, E05-US03.

---

## Implementation Order

```
1. E05-US01  PriceChartingClient              ← new core module; everything else depends on it
2. E05-US02  Dual auto-fetch with averaging   ← wires both clients into the scan pipeline
3. E05-US03  Split manual "Get Price" menu    ← UI change; depends on PriceChartingClient
```

---

## Feature 1 — E05-US01: PriceChartingClient

### Goal
New `core/pricecharting_client.py` that queries the PriceCharting API by card name +
set name and returns the loose (ungraded) market price.

### How PriceCharting lookup works
1. `GET /api/products?q=<card_name set_name number>&id=pokemon&api_key=<key>` — returns a
   list of matching products sorted by relevance; take the first hit.
   Including the card number in the query significantly narrows results and avoids
   hitting similarly-named cards from the same set (e.g. searching `"Charizard Phantasmal Flames 125"`
   picks the right card even if other Charizard prints exist in that set).
2. `GET /api/product?id=<product_id>&api_key=<key>` — returns price fields for that product.
   The relevant field is `loose-price` (integer, price in cents).
3. Final price = `loose-price / 100` (USD float).

If the API key is missing the client returns an unavailable result silently (no exception,
no log noise). If the search returns no products, the result is unavailable.

### New file: `core/pricecharting_client.py`

```python
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
        try:
            # Step 1: search
            resp = self._session.get(
                f"{self.BASE_URL}/products",
                params={"q": f"{card_name} {set_name} {number}", "id": "pokemon",
                        "api_key": self._api_key},
                timeout=config.API_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            products = resp.json().get("products", [])
            if not products:
                return PriceChartingResult(card_name=card_name, loose_price=None)

            # Step 2: fetch price for first hit
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
                fetched_at=datetime.now(timezone.utc).isoformat(), error=str(e),
            )
```

### `config.py` additions
```python
PRICECHARTING_API_KEY = os.getenv("PRICECHARTING_API_KEY", "")
```

---

## Feature 2 — E05-US02: Dual auto-fetch with averaging

### Goal
After every successful card identification, dispatch both price fetches concurrently.
Combine results when both settle (average, or single-source, or N/A). Emit one
`PriceUpdate` with the resolved price and a source label.

### Schema change — `scan_log`

Add one column:
```sql
ALTER TABLE scan_log ADD COLUMN price_source TEXT;
```
Values: `"avg"` | `"tcg"` | `"pc"` | `NULL` (N/A).
Migration applied at startup in `ScanLogger.__init__` via try/except as before.

### Price aggregation logic

```
tcg available + pc available  →  price = (tcg + pc) / 2,  source = "avg"
tcg available only            →  price = tcg,              source = "tcg"
pc available only             →  price = pc,               source = "pc"
neither                       →  price = None,             source = None
```

### Changes to `core/state_machine.py`

- Accept a `pc_client: PriceChartingClient | None = None` constructor param.
- After a successful match, instead of submitting one future, submit two:
  ```python
  tcg_future = self._executor.submit(self._price_client.fetch_price, card_id)
  pc_future  = self._executor.submit(self._pc_client.fetch_price, card_name, set_name, number)
              if self._pc_client else None
  self._pending_dual[scan_token] = (tcg_future, pc_future)
  ```
- In the drain loop (called each tick), check `_pending_dual` for settled pairs:
  ```python
  for token, (tcg_f, pc_f) in list(self._pending_dual.items()):
      tcg_done = tcg_f.done()
      pc_done  = pc_f is None or pc_f.done()
      if tcg_done and pc_done:
          tcg_price = tcg_f.result().market_price if tcg_done else None
          pc_price  = pc_f.result().loose_price   if pc_f and pc_f.done() else None
          price, source = _resolve(tcg_price, pc_price)
          self.price_update_queue.put(PriceUpdate(token, price, source))
          del self._pending_dual[token]
  ```
- `PriceUpdate` gains a `price_source: str | None` field.

### Changes to `core/scan_log.py`

- Add `price_source TEXT` column + migration.
- `update_price(scan_id, market_price, price_source=None)` — update both fields.

### Changes to `ui/log_panel.py`

- `update_price(tree_index, market_price, source=None)`:
  - Price string: `"$1.50 avg"` / `"$1.50 tcg"` / `"$1.50 pc"` / `"N/A"`
  - Source suffix is appended to the price cell value, not a separate column — no
    column layout changes needed.

### Changes to `ui/app_window.py`

- Instantiate `PriceChartingClient` and pass it to `ScanStateMachine`.
- `_handle_price_update` passes `update.price_source` down to `log.update_price()`
  and `logger.update_price()`.

---

## Feature 3 — E05-US03: Split manual "Get Price" menu

### Goal
Replace the single "Get Price" context menu item with two separate items,
each fetching from one source only and updating the row independently.

### Changes to `ui/log_panel.py`

Replace:
```python
menu.add_command(label="Get Price", ...)
```
With:
```python
menu.add_command(label="Get Price (TCGPlayer)",    command=lambda: cb_tcg(scan_id, idx))
menu.add_command(label="Get Price (PriceCharting)", command=lambda: cb_pc(scan_id, idx))
```

- Constructor params: `on_get_price_tcg=None`, `on_get_price_pc=None`
  (replaces the old `on_get_price`).

### Changes to `ui/app_window.py`

- `_on_get_price_tcg(scan_id, tree_index)`:
  - Fetch `card_id` from DB → submit `price_client.fetch_price(card_id)` to executor.
  - On result: update price with source `"tcg"`.

- `_on_get_price_pc(scan_id, tree_index)`:
  - Fetch `card_name`, `set_name`, `number` from DB → submit `pc_client.fetch_price(card_name, set_name, number)` to executor.
  - On result: update price with source `"pc"`.

- Both drain in `_drain_manual_prices()` which already handles the futures list;
  each entry gains a `source` field so the correct label is applied on completion.

---

## Summary of all file changes

| File | Changes |
|---|---|
| `config.py` | Add `PRICECHARTING_API_KEY` |
| `core/pricecharting_client.py` | **New file** — `PriceChartingResult` dataclass + `PriceChartingClient` |
| `core/state_machine.py` | Accept `pc_client`; dispatch dual futures; `PriceUpdate` gains `price_source`; add `_pending_dual` drain loop |
| `core/scan_log.py` | Add `price_source TEXT` column + migration; `update_price()` gains `price_source` param |
| `ui/log_panel.py` | `update_price()` accepts `source` and appends suffix; context menu splits into two items; constructor params updated |
| `ui/app_window.py` | Instantiate `PriceChartingClient`; pass to SM; `_handle_price_update` forwards source; two manual fetch handlers replace one |

---

## Open questions / assumptions

- **PriceCharting search accuracy**: lookup is by `"{card_name} {set_name} {number}"`.
  Including the number reduces false matches significantly. Still text-based so edge cases
  may exist, but empirically confirmed to work (e.g. `"Charizard Phantasmal Flames 125"`).
- **PriceCharting API key**: user must add `PRICECHARTING_API_KEY=...` to `.env`.
  If absent, PC fetches silently skip and auto-fetches use TCGPlayer only (source = `"tcg"`).
- **Individual source prices are not stored**: only the resolved/averaged price + source
  label are written to `scan_log`. Raw TCG and PC prices are not persisted.
- **Manual fetch replaces, not averages**: "Get Price (TCGPlayer)" overwrites the current
  price with the TCG result (source = `"tcg"`), regardless of whether a PC price exists.
  Same for the PC option. This keeps the manual flow simple and predictable.

---

## Verification checklist

- [ ] Auto-scan: both fetches dispatch concurrently; price column shows `$X.XX avg` when both succeed
- [ ] Auto-scan: TCGPlayer timeout → price shows `$X.XX pc` using PC value alone
- [ ] Auto-scan: both fail → price shows `N/A`
- [ ] No `PRICECHARTING_API_KEY` in `.env` → PC fetches skip silently; no crash
- [ ] Right-click → "Get Price (TCGPlayer)" updates price with `tcg` suffix
- [ ] Right-click → "Get Price (PriceCharting)" updates price with `pc` suffix
- [ ] `price_source` column persists in `scan_log.db` and survives app restart
- [ ] Existing DB rows (no `price_source`) open without error (migration adds nullable column)
