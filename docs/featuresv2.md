# Features v2 — Implementation Plan

Covers stories: E01-US09, E02-US10, E04-US01, E04-US02, E04-US03.

---

## Implementation Order (critical path)

```
1. E01-US09  Unique scan ID + capture image      ← foundation; E04-US03 depends on it
2. E02-US10  180° rotation fallback              ← independent; can slot in anywhere
3. E04-US01  Card reference images (build_db)    ← independent; prerequisite for E04-US03
4. E04-US02  Right-click → Get Price             ← needs context menu wired up first
5. E04-US03  Right-click → Manual Remap          ← depends on #1, #3, and #4's menu work
```

---

## Feature 1 — E01-US09: Unique scan ID + capture image

### Goal
Persist `scan_token` (UUID) to `scan_log.db` and save the cropped card image to
`db/captures/{scan_token}.jpg` at scan time. This links every log row to its capture.

### Schema change — `scan_log`
Add one column:
```sql
ALTER TABLE scan_log ADD COLUMN scan_token TEXT;
```
Migration is applied at startup via `ScanLogger.__init__` using a try/except
(`ALTER TABLE` on a column that already exists raises `OperationalError` in SQLite).
Existing rows will have `scan_token = NULL` — that is acceptable.

### File changes

**`config.py`**
```python
CAPTURES_DIR = "db/captures"
```

**`core/scan_log.py`**
- Add `scan_token: str | None = None` to `ScanRecord`
- Update `_CREATE_SCAN_LOG` to include `scan_token TEXT`
- Add migration in `__init__`:
  ```python
  try:
      self._conn.execute("ALTER TABLE scan_log ADD COLUMN scan_token TEXT")
      self._conn.commit()
  except sqlite3.OperationalError:
      pass   # column already exists
  ```
- Update `log_scan()` INSERT to include `scan_token`
- Add `get_scan(scan_id: int) -> sqlite3.Row` method (needed by remap dialog)

**`core/state_machine.py`**
- After a successful match, before `result_queue.put(scan_result)`:
  ```python
  os.makedirs(config.CAPTURES_DIR, exist_ok=True)
  capture_path = os.path.join(config.CAPTURES_DIR, f"{scan_token}.jpg")
  try:
      self._last_crop.save(capture_path)
  except Exception:
      pass
  ```
- Store `self._last_crop: Image.Image | None` — set in STABILIZING when the canonical
  hash is selected, so the matching crop is always available.

**`ui/app_window.py`**
- Pass `result.scan_token` into `ScanRecord` when calling `log_scan`.

### New directory
`db/captures/` — created on first scan via `os.makedirs(..., exist_ok=True)`.

---

## Feature 2 — E02-US10: 180° rotation fallback

### Goal
If the initial hash match fails, rotate the crop 180° and try again. Silent — no
extra log noise.

### How rotation works
Hashes cannot be rotated directly. We need the original crop image to recompute.
`self._last_crop` (added in Feature 1) provides this at match time.

### File changes

**`core/matcher.py`**
- Add optional `fallback_hash` parameter to `find_matches`:
  ```python
  def find_matches(self, query_hash, fallback_hash=None) -> MatchResult | None:
      result = self._scan(query_hash)
      if result is None and fallback_hash is not None:
          result = self._scan(fallback_hash)
      return result

  def _scan(self, query_hash) -> MatchResult | None:
      # existing full-table loop, extracted into this helper
  ```

**`core/state_machine.py`**
- In MATCHING state, before submitting to executor, compute the 180° hash:
  ```python
  crop_180 = self._last_crop.rotate(180) if self._last_crop else None
  hash_180 = compute_phash(crop_180) if crop_180 else None
  self._match_future = self._executor.submit(
      self._matcher.find_matches, canonical, hash_180
  )
  ```
- No changes to result handling — a rotated match looks identical to a normal one.

---

## Feature 3 — E04-US01: Card reference images

### Goal
`build_db.py` downloads the small card image for every card and saves it to
`db/images/{card_id}.png`. Resumable.

### File changes

**`build_db.py`**
- After existing hash computation, add a parallel image download pass
  (or fold it into the existing `ThreadPoolExecutor` worker):
  ```python
  img_path = os.path.join(IMAGES_DIR, f"{card_id}.png")
  if not os.path.exists(img_path):
      response = requests.get(image_url, timeout=10)
      response.raise_for_status()
      with open(img_path, "wb") as f:
          f.write(response.content)
  ```
- Create `db/images/` at script start: `os.makedirs(IMAGES_DIR, exist_ok=True)`
- Failures are caught per-card; failed image downloads are counted in the summary
  but do not prevent the card record from being written.

**`config.py`**
```python
IMAGES_DIR = "db/images"
```

### Notes
- ~20k images, expect 200–600 MB depending on card art size.
- First-run download adds significant time; subsequent runs skip existing files.
- The existing `image_url` column in `cards.db` provides the download URL.

---

## Feature 4 — E04-US02: Right-click → Get Price

### Goal
Right-clicking a scan log row shows a context menu. "Get Price" forces a fresh
price fetch and updates the row.

### File changes

**`ui/log_panel.py`**
- Bind `<Button-3>` (right-click) on the `ttk.Treeview`:
  ```python
  self._tree.bind("<Button-3>", self._on_right_click)
  ```
- `_on_right_click(event)`: identify the row under the cursor, build a
  `tk.Menu`, post it with items:
  - "Get Price" → fires `self._on_get_price(scan_id, tree_index)` callback
  - "Remap Card" → fires `self._on_remap(scan_id, tree_index)` callback (E04-US03)
- Add `on_get_price` and `on_remap` constructor parameters alongside existing
  `on_ambiguous_click`.
- `update_price_loading(tree_index)`: sets the price cell to "…" while fetching.

**`ui/app_window.py`**
- Pass new callbacks when constructing `LogPanel`.
- `_on_get_price(scan_id, tree_index)`:
  1. Read `card_id` from the log row (fetch via `self._logger.get_scan(scan_id)`)
  2. Show loading indicator in price column via `self._log.update_price_loading(tree_index)`
  3. Submit `self._price_client.fetch_price(card_id)` to a local
     `ThreadPoolExecutor` (reuse existing one on state machine, or add a small
     one to `AppWindow`)
  4. Drain result in `_tick()` via a `price_update_queue` already in place —
     repurpose the existing `PriceUpdate` + `_handle_price_update` path by
     registering the manual fetch in `_pending_price_map` the same way auto-fetches are.

---

## Feature 5 — E04-US03: Right-click → Manual Remap

### Goal
Opens a dialog showing the saved capture image and the top N closest card matches
(with thumbnails). User picks the correct card; log row is updated and a price
fetch is triggered.

### New file: `ui/remap_dialog.py`

```
RemapDialog(parent, scan_id, scan_token, matcher, logger, price_client, on_resolved, remap_n)
```

Layout (top-to-bottom):
```
┌─────────────────────────────────────────┐
│  Captured image     │  N slider (10-500) │
│  (db/captures/...)  │                    │
├─────────────────────────────────────────┤
│  Scrollable list of top-N matches:       │
│  [thumbnail] Name — Set #Num  dist=XX    │
│  ...                                     │
├─────────────────────────────────────────┤
│  [Cancel]                    [Confirm]   │
└─────────────────────────────────────────┘
```

- Capture image loaded from `db/captures/{scan_token}.jpg`; if missing, shows a
  placeholder.
- Match list populated by a new `matcher.find_top_n(query_hash, n)` call (see below).
  The query hash is recomputed from the capture image at dialog open time.
- Card thumbnails loaded from `db/images/{card_id}.png`; missing thumbnails show a
  grey placeholder.
- "Confirm" is disabled until a row is selected.
- On confirm: calls `on_resolved(scan_id, selected_card, tree_index)` which updates
  the log row, marks `is_corrected=1`, and triggers a price fetch.

### File changes

**`core/matcher.py`**
- Add `find_top_n(query_hash, n: int = 100) -> list[CardCandidate]`:
  ```python
  def find_top_n(self, query_hash, n: int = 100) -> list[CardCandidate]:
      results = []
      for db_hash, card in self._cards:
          dist = int(query_hash - db_hash)
          results.append(CardCandidate(..., hamming_dist=dist))
      results.sort(key=lambda c: c.hamming_dist)
      return results[:n]
  ```
  Ignores `MATCH_HAMMING_THRESHOLD` — returns the best N regardless of distance.

**`ui/log_panel.py`**
- `_on_remap` fires `self._on_remap(scan_id, tree_index)`.

**`ui/app_window.py`**
- `_on_remap(scan_id, tree_index)`:
  1. Fetch full scan row via `self._logger.get_scan(scan_id)` to get `scan_token`
  2. Open `RemapDialog`; pass `self._state_machine._matcher` (or expose matcher
     via a getter) so the dialog can call `find_top_n`
  3. `on_resolved` callback: call `self._logger.resolve(...)`, update log panel,
     trigger price fetch via same manual-fetch path as E04-US02.

**`config.py`**
```python
REMAP_TOP_N = 100   # default N for manual remap candidate list
```

---

## Summary of all file changes

| File | Changes |
|---|---|
| `config.py` | Add `CAPTURES_DIR`, `IMAGES_DIR`, `REMAP_TOP_N` |
| `build_db.py` | Download + save card images to `db/images/` |
| `core/scan_log.py` | Add `scan_token` column + migration; add `get_scan()`; update `log_scan()` |
| `core/matcher.py` | Refactor `find_matches` → `_scan` helper; add `fallback_hash` param; add `find_top_n()` |
| `core/state_machine.py` | Store `_last_crop`; save capture image; pass `hash_180` to matcher |
| `ui/log_panel.py` | Right-click context menu; `on_get_price` + `on_remap` callbacks; `update_price_loading()` |
| `ui/app_window.py` | Wire `on_get_price`, `on_remap`; pass `scan_token` to `ScanRecord` |
| `ui/remap_dialog.py` | New file — capture viewer + top-N match list + confirm/cancel |

---

## Verification checklist

- [ ] Every scan writes a `.jpg` to `db/captures/` and a `scan_token` row to the DB
- [ ] Dropping a card upside-down matches correctly via 180° fallback
- [ ] `build_db.py` populates `db/images/` and is resumable
- [ ] Right-click → "Get Price" updates a previously failed price row
- [ ] Right-click → "Remap Card" opens dialog with capture image + thumbnails
- [ ] Selecting a card in remap updates the log row and triggers a price fetch
- [ ] All right-click actions work on both old rows (no capture) and new rows
