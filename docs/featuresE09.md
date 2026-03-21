# Features E09 — Holo Type Tracking

Covers stories: E09-US01, E09-US02, E09-US03.

---

## Design summary

Every scan now tracks which TCGPlayer price variants exist for a card (`holo_variants`)
and which one is currently applied (`holo_type`). A new "Holo" column in the log panel
shows this at a glance. Users can right-click any row to pick a specific variant, which
triggers a fresh price re-fetch. A batch dropdown lets users pre-select a variant for all
incoming scans — useful when processing a stack of reverse holos.

---

## Variant abbreviations (used throughout UI)

| API key | Display |
|---|---|
| `normal` | `normal` |
| `holofoil` | `holo` |
| `reverseHolofoil` | `rev holo` |
| `1stEditionHolofoil` | `1st ed holo` |
| `unlimitedHolofoil` | `unltd holo` |

Defined as a module-level dict in `core/price_client.py` and imported wherever needed.

---

## Implementation order

```
1. core/price_client.py   — extend PriceResult; support targeted variant fetch
2. core/scan_log.py       — add holo_type + holo_variants columns; migration; export
3. core/state_machine.py  — pass holo_mode through; extend PriceUpdate
4. ui/app_window.py       — batch dropdown; wire holo data through price flow; set-holo-type action
5. ui/log_panel.py        — new Holo column; right-click submenu; update/reset helpers
```

---

## 1 — `core/price_client.py`

### New module-level constant

```python
VARIANT_ABBREV = {
    "normal":               "normal",
    "holofoil":             "holo",
    "reverseHolofoil":      "rev holo",
    "1stEditionHolofoil":   "1st ed holo",
    "unlimitedHolofoil":    "unltd holo",
}
VARIANT_PRIORITY = list(VARIANT_ABBREV.keys())
```

### `PriceResult` gains two fields

```python
@dataclass
class PriceResult:
    ...
    price_variant: str | None = None          # which variant was used
    available_variants: list = field(default_factory=list)  # all variants in response
```

### `_extract_prices` becomes variant-aware

```python
def _extract_prices(self, prices: dict,
                    target: str | None = None
                    ) -> tuple[float|None, float|None, float|None, str|None, list]:
    available = [v for v in VARIANT_PRIORITY if v in prices]
    # also include any variants not in our known list
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
```

### `fetch_price` gains `target_variant` parameter

```python
def fetch_price(self, card_id: str,
                target_variant: str | None = None) -> PriceResult:
    ...
    market, low, high, used_variant, available = self._extract_prices(
        data.get("tcgplayer", {}).get("prices", {}),
        target=target_variant,
    )
    return PriceResult(
        card_id=card_id,
        market_price=market,
        low_price=low,
        high_price=high,
        price_variant=used_variant,
        available_variants=available,
        ...
    )
```

---

## 2 — `core/scan_log.py`

### Schema additions

```sql
holo_type     TEXT,   -- e.g. "reverseHolofoil"; NULL = not yet set / automatic
holo_variants TEXT    -- JSON array e.g. '["normal","reverseHolofoil"]'
```

### Migration (same try/except pattern as existing migrations)

```python
for migration in [
    "ALTER TABLE scan_log ADD COLUMN scan_token TEXT",
    "ALTER TABLE scan_log ADD COLUMN price_source TEXT",
    "ALTER TABLE scan_log ADD COLUMN holo_type TEXT",
    "ALTER TABLE scan_log ADD COLUMN holo_variants TEXT",
]:
    try:
        self._conn.execute(migration)
        self._conn.commit()
    except sqlite3.OperationalError:
        pass
```

### New method: `update_holo`

```python
def update_holo(self, scan_id: int,
                holo_type: str | None,
                holo_variants: list | None) -> None:
    import json
    self._conn.execute(
        "UPDATE scan_log SET holo_type=?, holo_variants=? WHERE id=?",
        (holo_type,
         json.dumps(holo_variants) if holo_variants is not None else None,
         scan_id),
    )
    self._conn.commit()
```

### `export_json` and `export_csv` — include `holo_type` and `holo_variants`

The existing column list in `get_session_scans` already uses `SELECT *`-style so the new
columns appear automatically. Verify CSV export column list and add both fields.

---

## 3 — `core/state_machine.py`

### `PriceUpdate` gains holo fields

```python
@dataclass
class PriceUpdate:
    ...
    price_variant: str | None = None
    available_variants: list = field(default_factory=list)
```

### `ScanStateMachine.__init__` gains `holo_mode`

```python
def __init__(self, ..., holo_mode: str = "Automatic"):
    self._holo_mode = holo_mode   # can be updated mid-session
```

Expose as a settable property:
```python
@property
def holo_mode(self) -> str:
    return self._holo_mode

@holo_mode.setter
def holo_mode(self, value: str) -> None:
    self._holo_mode = value
```

### Price fetch passes target variant

```python
target = None if self._holo_mode == "Automatic" else self._holo_mode
tcg_future = self._executor.submit(
    self._price_client.fetch_price, result.primary.card_id, target
)
```

### `_drain_price_futures` forwards holo data into `PriceUpdate`

```python
self.price_update_queue.put(PriceUpdate(
    ...
    price_variant=tcg_result.price_variant,
    available_variants=tcg_result.available_variants,
))
```

---

## 4 — `ui/app_window.py`

### Batch dropdown (in `_build_ui`, `btn_row`, before scan button)

```python
from tkinter import ttk

HOLO_OPTIONS = ["Automatic", "normal", "holofoil",
                "reverseHolofoil", "1stEditionHolofoil", "unlimitedHolofoil"]

self._holo_mode_var = tk.StringVar(value="Automatic")
ttk.Combobox(
    btn_row,
    textvariable=self._holo_mode_var,
    values=HOLO_OPTIONS,
    state="readonly",
    width=14,
    font=("Helvetica", 9),
).pack(side="left", padx=(0, 6), pady=4)
self._holo_mode_var.trace_add("write", self._on_holo_mode_change)
```

### `_on_holo_mode_change`

```python
def _on_holo_mode_change(self, *_) -> None:
    if self._state_machine:
        mode = self._holo_mode_var.get()
        self._state_machine.holo_mode = mode
```

### `_handle_price_update` — forward holo fields

```python
def _handle_price_update(self, update: PriceUpdate) -> None:
    ...
    self._logger.update_holo(scan_id, update.price_variant, update.available_variants)
    self._log.update_holo(tree_index, update.price_variant, update.available_variants,
                          forced=self._holo_mode_var.get() != "Automatic")
```

The `forced` flag lets the log panel know whether to show a warning when the targeted
variant wasn't found (i.e. `price_variant is None` but a specific variant was requested).

### `_on_set_holo_type(scan_id, tree_index, variant)`

Called when user picks a variant from the right-click submenu.

```python
def _on_set_holo_type(self, scan_id: int, tree_index: int, variant: str) -> None:
    row = self._logger.get_scan(scan_id)
    if row is None:
        return
    self._log.update_price_loading(tree_index)
    self._log.update_holo_loading(tree_index, variant)
    future = self._executor.submit(
        self._price_client.fetch_price, row["card_id"], variant
    )
    self._manual_price_futures.append((future, scan_id, tree_index, "tcg", False, variant))
```

The existing `_drain_manual_prices` tuple grows by one element (target variant) so it
can call `update_holo` after the fetch settles.

---

## 5 — `ui/log_panel.py`

### New column

Insert `"holo"` between `"price"` and `"flags"`:

```python
cols = ("time", "name", "set", "number", "rarity", "price", "holo", "flags")
```

Width: `80px`, stretch `False`.

### New parallel list

```python
self._holo_variants: list[list] = []   # available_variants per row
```

Populated in `append()`, `clear()`, `load_session()`.

### Helper: `_fmt_holo_cell(variants, active_variant, forced_variant=None)`

Module-level helper:

```python
from core.price_client import VARIANT_ABBREV

def _fmt_holo_cell(variants: list, active: str | None,
                   forced: str | None = None) -> tuple[str, str | None]:
    """Return (cell_text, tag_or_None)."""
    if not variants:
        return "N/A", None
    if forced and active is None:
        # forced variant not found in API response
        abbrev = VARIANT_ABBREV.get(forced, forced)
        return f"? {abbrev}", "holo_warn"
    if active:
        abbrev = VARIANT_ABBREV.get(active, active)
        return abbrev, None
    # Automatic, multiple variants
    parts = [VARIANT_ABBREV.get(v, v) for v in variants]
    return " · ".join(parts), None
```

Tag `"holo_warn"` configured in `_build()`:
```python
self._tree.tag_configure("holo_warn", foreground="#ffaa00")
```

### `update_holo(tree_index, active_variant, available_variants, forced=False)`

```python
def update_holo(self, tree_index: int, active_variant: str | None,
                available_variants: list, forced: bool = False) -> None:
    if tree_index >= len(self._all_iids):
        return
    iid = self._all_iids[tree_index]
    if tree_index < len(self._holo_variants):
        self._holo_variants[tree_index] = available_variants
    forced_variant = None  # caller passes forced flag but not the specific name;
    # to show the warning we need the name — app_window should pass it explicitly.
    # Simplest fix: change signature to forced_variant: str | None = None
    text, tag = _fmt_holo_cell(available_variants, active_variant, forced_variant)
    vals = list(self._tree.item(iid, "values"))
    vals[6] = text   # holo column index
    tags = (tag,) if tag else self._tree.item(iid, "tags")
    self._tree.item(iid, values=vals, tags=tags)
```

> Note: `update_holo` signature should use `forced_variant: str | None = None` instead
> of a bool, so the warning cell can include the variant name. Adjust app_window call
> accordingly.

### `update_holo_loading(tree_index, variant)`

```python
def update_holo_loading(self, tree_index: int, variant: str) -> None:
    if tree_index >= len(self._all_iids):
        return
    iid = self._all_iids[tree_index]
    abbrev = VARIANT_ABBREV.get(variant, variant)
    vals = list(self._tree.item(iid, "values"))
    vals[6] = abbrev   # show chosen variant immediately; price shows "..."
    self._tree.item(iid, values=vals)
```

### Right-click menu addition

In `_on_right_click`, after the existing menu items:

```python
variants = self._holo_variants[idx] if idx < len(self._holo_variants) else []
if variants:
    holo_menu = tk.Menu(menu, tearoff=0)
    for v in variants:
        abbrev = VARIANT_ABBREV.get(v, v)
        holo_menu.add_command(
            label=abbrev,
            command=lambda vv=v: self._on_set_holo_type(scan_id, idx, vv)
                                 if self._on_set_holo_type else None,
        )
    menu.add_cascade(label="Set Holo Type", menu=holo_menu)
```

Constructor gains `on_set_holo_type=None` parameter.

### Column index ripple

The new `"holo"` column is index 6; `"flags"` shifts to index 7. All existing
`vals[6]` references in `update_price`, `update_resolved` etc. must shift to `vals[7]`.
Safer to reference by slice rather than hardcoded index, or define column index constants.

---

## Summary of all file changes

| File | Changes |
|---|---|
| `core/price_client.py` | `VARIANT_ABBREV`, `VARIANT_PRIORITY`; extend `PriceResult`; variant-aware `_extract_prices`; `target_variant` param on `fetch_price` |
| `core/scan_log.py` | Add `holo_type`, `holo_variants` columns; migration; `update_holo()`; export |
| `core/state_machine.py` | `holo_mode` property on `ScanStateMachine`; pass target to price fetch; extend `PriceUpdate` |
| `ui/app_window.py` | Batch Combobox in btn_row; `_on_holo_mode_change`; extend `_handle_price_update`; `_on_set_holo_type`; extend `_drain_manual_prices` tuple |
| `ui/log_panel.py` | New `"holo"` column; `_holo_variants` list; `_fmt_holo_cell` helper; `update_holo`; `update_holo_loading`; right-click cascade; column index shift for `"flags"` |

---

## Verification checklist

- [ ] Scanning Metang Crown Zenith #90: Holo cell shows `normal · rev holo` in Automatic mode
- [ ] Price shown is normal ($0.07), not reverse holo
- [ ] Right-click → Set Holo Type → `rev holo`: price re-fetches and updates to $0.32
- [ ] Holo cell shows `rev holo` after selection; `holo_type` in DB updated
- [ ] Batch dropdown set to `reverseHolofoil`: next scan prices at reverse holo automatically
- [ ] Card with no `reverseHolofoil` variant scanned in `reverseHolofoil` mode: Holo cell shows `? rev holo` in orange; price shows N/A
- [ ] Remapping a row resets holo cell to "..." then repopulates after re-fetch
- [ ] Holo type and variants appear in CSV export
- [ ] Holo type and variants appear in JSON session export and reload correctly
- [ ] Flags column still works after column index shift
- [ ] `update_price`, `update_resolved`, `update_price_loading` all reference correct column indices
