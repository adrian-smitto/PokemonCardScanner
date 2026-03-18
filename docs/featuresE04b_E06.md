# Features E04b + E06 — Implementation Plan

Covers stories: E04-US05, E04-US06, E04-US07, E06-US01, E06-US02.

---

## Implementation Order

```
1. E04-US06  Double-click unknown → remap     ← tiny; log_panel + card_id tracking only
2. E04-US07  Bulk fetch progress indicator    ← tiny; app_window status bar only
3. E04-US05  Filter candidates in remap       ← self-contained; remap_dialog only
4. E06-US01  Save session to JSON             ← scan_log read + file write
5. E06-US02  Load session from JSON           ← log_panel population + view-only mode
```

---

## Feature 1 — E04-US06: Double-click unknown row opens remap

### Goal
Double-clicking an "Unknown" log row opens the remap dialog directly, skipping the
right-click menu.

### File changes

**`ui/log_panel.py`**
- Add `_card_ids: list[str] = []` parallel to the other per-row lists.
- `append()`: store `result.card_id` into `_card_ids`.
- `clear()`: reset `_card_ids = []`.
- Bind `<Double-ButtonRelease-1>` → `_on_double_click(event)`.
- `_on_double_click`: identify the row, check `_card_ids[idx] == ""`, fire
  `self._on_remap(scan_id, scan_token, idx)` if so. Does nothing on non-unknown rows.

No changes needed to `app_window.py` — `_on_remap` callback is already wired.

---

## Feature 2 — E04-US07: Bulk fetch progress indicator

### Goal
A label in the status bar counts down remaining bulk price fetches and disappears
when done. Single right-click fetches do not trigger it.

### File changes

**`ui/app_window.py`**
- Add `self._bulk_pending: int = 0` instance variable.
- Add a `tk.StringVar` + `tk.Label` in the status bar (hidden initially via
  `label.pack_forget()`).
- `_fetch_missing_prices()`: set `_bulk_pending = len(unpriced)` before submitting;
  tag each future with `bulk=True` so the drain knows which counter to decrement.
  Show the label: `_bulk_label.pack(...)`.
- To distinguish bulk from single fetches, change `_manual_price_futures` entries
  from `(future, scan_id, tree_index, source)` to
  `(future, scan_id, tree_index, source, is_bulk: bool)`.
- `_drain_manual_prices()`: when a bulk future settles, decrement `_bulk_pending`
  and update the label text. When `_bulk_pending` reaches 0, hide the label.

---

## Feature 3 — E04-US05: Filter candidates in remap dialog

### Goal
A text entry above the candidate treeview filters rows by card name as you type.
Uses ttk.Treeview's `detach`/`reattach` to show/hide rows without discarding data.

### File changes

**`ui/remap_dialog.py`**
- In `_build()`, insert a filter bar above the treeview:
  ```python
  filter_bar = tk.Frame(list_frame, bg="#1e1e1e")
  filter_bar.pack(fill="x", pady=(0, 4))
  tk.Label(filter_bar, text="Filter:", ...).pack(side="left")
  self._filter_var = tk.StringVar()
  self._filter_var.trace_add("write", self._on_filter_change)
  tk.Entry(filter_bar, textvariable=self._filter_var, ...).pack(side="left", fill="x", expand=True)
  tk.Button(filter_bar, text="✕", command=lambda: self._filter_var.set("")).pack(side="left")
  ```
- Add `self._all_iids: list[str] = []` — ordered list of all treeview item IDs,
  populated in `_refresh_list()`.
- `_on_filter_change(*_)`:
  ```python
  query = self._filter_var.get().lower()
  for iid in self._all_iids:
      vals = self._tree.item(iid, "values")
      name = vals[1].lower()   # "name" column
      if query in name:
          self._tree.reattach(iid, "", "end")
      else:
          self._tree.detach(iid)
  ```
- `_refresh_list()`: reset `_filter_var` to `""` so the filter clears when N changes.
- `_on_select()`: index lookup must use visible items only:
  ```python
  items = list(self._tree.get_children())   # only attached items
  self._selected_idx = self._all_iids.index(sel[0])  # index into full list
  ```
  This ensures `_candidates[self._selected_idx]` always maps correctly regardless
  of filtering.

---

## Feature 4 — E06-US01: Save session to JSON

### Goal
Serialize all current-session scan rows to a `.json` file. No DB writes; pure read
+ file output.

### File changes

**`core/scan_log.py`**
- Add `export_json(session_id: str, filepath: str) -> None`:
  ```python
  rows = self.get_session_scans(session_id)
  data = [dict(row) for row in rows]
  with open(filepath, "w", encoding="utf-8") as f:
      json.dump({"version": 1, "scans": data}, f, indent=2)
  ```

**`ui/app_window.py`**
- Add "Save Session" button in the status bar next to "Export CSV".
- `_save_session()`: open save dialog (`.json`), call `self._logger.export_json(...)`.

---

## Feature 5 — E06-US02: Load session from JSON

### Goal
Populate the log panel from a saved `.json` file. View-only — no DB writes,
no price fetches, no scan_count/total updates.

### Design decisions
- Loaded rows are display-only in the sense that no prices are auto-fetched on load.
  However, all manual actions still work: right-click "Get Price", "Fetch Missing Prices",
  and "Remap Card" all operate normally on loaded rows — they look up `scan_id` in
  `scan_log.db` exactly as they would for live session rows.
- The current live session is cleared from the log panel before loading. A warning
  dialog confirms this if the current session has unsaved rows.
- `scan_id` from the file is stored in `_scan_ids` so remap can call
  `self._logger.get_scan(scan_id)` — this works as long as the original `scan_log.db`
  is present. If the DB row is gone, remap gracefully shows no capture.

### File changes

**`ui/log_panel.py`**
- Add `load_session(rows: list[dict]) -> None`:
  - Calls `clear()` first.
  - For each row, inserts a treeview entry with the stored values.
  - Applies "unknown" tag where `card_id == ""`.
  - Applies "corrected" flag text where `is_corrected == 1`.
  - Populates `_scan_ids`, `_scan_tokens`, `_card_ids`, `_candidate_counts`
    from the row dict. Candidate count can be stored in the JSON or defaulted to 0.

**`ui/app_window.py`**
- Add "Load Session" button in the status bar.
- `_load_session()`:
  1. If current session has rows, show a confirmation dialog.
  2. Open file dialog (`.json`).
  3. Parse JSON, validate `version` field.
  4. Call `self._log.load_session(data["scans"])`.
  5. Recalculate `_total_value` and `_scan_count` from loaded rows.
  6. Do NOT add anything to `_pending_price_map` — loaded rows are view-only.

---

## Summary of all file changes

| File | Changes |
|---|---|
| `ui/log_panel.py` | Add `_card_ids`; double-click handler; `load_session()`|
| `ui/remap_dialog.py` | Filter bar + Entry; `_all_iids` tracking; `_on_filter_change()`; fix `_on_select` indexing |
| `ui/app_window.py` | Bulk indicator label + `_bulk_pending` counter; Save/Load Session buttons + handlers; `_manual_price_futures` gains `is_bulk` field |
| `core/scan_log.py` | Add `export_json()` |

---

## Verification checklist

- [ ] Double-clicking an Unknown row opens remap; double-clicking a matched row does nothing
- [ ] Typing in the remap filter narrows the list; clearing restores it; N-refresh resets filter
- [ ] Selecting a filtered-out card (via keyboard) does not map to the wrong candidate
- [ ] "Fetch Missing Prices" shows the countdown label; it disappears when all settle
- [ ] Single right-click "Get Price" does not affect the bulk counter
- [ ] "Save Session" produces a valid JSON file with all session rows
- [ ] "Load Session" populates the log panel; unknown rows are styled correctly
- [ ] Loading warns if current session would be lost
- [ ] Loading a malformed file shows an error and does not crash
