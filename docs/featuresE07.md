# Features E07 — UI Polish & Quick Actions

Covers stories: E07-US01, E07-US02, E07-US03, E07-US04.

---

## Implementation Order

```
1. E07-US01  Double-click to confirm in remap     ← 3 lines; remap_dialog only
2. E07-US04  Persist window positions             ← settings.json read/write; 3 files touched
3. E07-US02  Filter log: unknown only             ← requires _all_iids refactor in log_panel
4. E07-US03  Filter log: has alts only            ← same refactor; add second checkbox
```

Items 3 and 4 share the same prerequisite internal change and are implemented together.

---

## Feature 1 — E07-US01: Double-click to confirm in remap dialog

### Goal
Double-clicking a candidate row in `RemapDialog` immediately selects it and fires `_confirm()`,
saving the user a click.

### File changes

**`ui/remap_dialog.py`**
- In `_build()`, add a second binding on the treeview:
  ```python
  self._tree.bind("<Double-ButtonRelease-1>", self._on_double_click)
  ```
- Add `_on_double_click(self, event)`:
  ```python
  def _on_double_click(self, event) -> None:
      # _on_select already fires on the preceding ButtonRelease-1,
      # so _selected_idx is already set by the time the double-click fires.
      # Just confirm if something is selected.
      if self._selected_idx is not None:
          self._confirm()
  ```
  Note: `<<TreeviewSelect>>` fires on single click. By the time `<Double-ButtonRelease-1>`
  fires, `_on_select` has already run and `_selected_idx` is set. No re-selection needed.

---

## Feature 2 — E07-US04: Persist window positions

### Goal
Save `geometry()` strings for the main window, remap dialog, and resolution dialog to
`settings.json`; restore them on next open.  Off-screen positions (monitor removed) are
detected and silently ignored.

### Settings keys
| Key | Window |
|---|---|
| `"main_geometry"` | `AppWindow._root` |
| `"remap_geometry"` | `RemapDialog._win` |
| `"resolution_geometry"` | `ResolutionDialog` (self) |

### Off-screen guard
```python
def _is_on_screen(geometry: str) -> bool:
    """Return False if the top-left corner is off all available monitors."""
    import re
    m = re.match(r'\d+x\d+\+(-?\d+)\+(-?\d+)', geometry)
    if not m:
        return False
    x, y = int(m.group(1)), int(m.group(2))
    return x > -2000 and y > -2000   # simple sanity check; adjust if needed
```
A more robust check is possible with `winfo_screenwidth/height`, but the simple threshold
is sufficient for single- or dual-monitor setups.

### File changes

**`ui/app_window.py`**
- In `__init__`, after building the root window, restore geometry:
  ```python
  geo = load_setting("main_geometry", None)
  if geo and _is_on_screen(geo):
      self._root.geometry(geo)
  ```
- In `_on_close()`, before saving other settings:
  ```python
  save_setting("main_geometry", self._root.geometry())
  ```

**`ui/remap_dialog.py`**
- In `__init__`, after `self._win = tk.Toplevel(parent)` and configure calls:
  ```python
  from core.roi import load_setting, save_setting
  geo = load_setting("remap_geometry", None)
  if geo and _is_on_screen(geo):
      self._win.geometry(geo)
  ```
- Save on close. The dialog closes via `_confirm()` and the Cancel button.
  Replace direct `self._win.destroy()` calls with a `_close()` helper:
  ```python
  def _close(self) -> None:
      save_setting("remap_geometry", self._win.geometry())
      self._win.destroy()
  ```
  Update `_confirm()` and the Cancel button command to call `self._close()`.
  Also bind `WM_DELETE_WINDOW`: `self._win.protocol("WM_DELETE_WINDOW", self._close)`.

**`ui/resolution_dialog.py`**
- Same pattern as remap: restore geometry in `__init__`, save in a `_close()` helper
  called from all exit paths (confirm, cancel, `WM_DELETE_WINDOW`).

**`ui/app_window.py`** (module-level helper)
- Add `_is_on_screen(geometry: str) -> bool` as a module-level function (or import from
  a shared location). Since remap_dialog and resolution_dialog also need it, the simplest
  approach is to duplicate the 5-line function in each file to avoid a new dependency.
  Alternatively, add it to `core/roi.py` alongside `load_setting`/`save_setting`.

  **Recommended**: add to `core/roi.py` and import where needed.

---

## Feature 3+4 — E07-US02 & E07-US03: Log panel row filters

### Goal
Two checkboxes above the log treeview: "Unknown only" and "Has alts".
Each independently hides/shows rows using `detach`/`reattach`.

### Prerequisite: `_all_iids` refactor in `log_panel.py`

Currently `update_price`, `update_resolved`, and `update_price_loading` look up rows via:
```python
items = self._tree.get_children()
iid = items[tree_index]
```
`get_children()` returns only **attached** (visible) items. When a filter is active and rows
are detached, this index shifts and the wrong row gets updated.

Fix: maintain `self._all_iids: list[str] = []` in parallel with `_scan_ids` and use it
for all index-based lookups, exactly as `remap_dialog.py` does.

**Changes to existing methods:**
- `append()`: `self._all_iids.append(iid)`
- `clear()`: `self._all_iids = []`
- `load_session()`: `self._all_iids.append(iid)`
- `update_price(tree_index, ...)`: use `iid = self._all_iids[tree_index]`
- `update_resolved(tree_index, ...)`: use `iid = self._all_iids[tree_index]`
- `update_price_loading(tree_index, ...)`: use `iid = self._all_iids[tree_index]`
- `get_unpriced_rows()`: iterate `self._all_iids` (not `get_children()`) so detached
  N/A rows are still returned for bulk price fetch.

### Filter bar UI

In `_build()`, insert a filter bar **above** the treeview (before the scrollbar/tree packing):
```python
filter_bar = tk.Frame(self, bg="#1e1e1e")
filter_bar.pack(fill="x", padx=4, pady=(4, 2))

self._filter_unknown_var = tk.BooleanVar(value=False)
tk.Checkbutton(
    filter_bar, text="Unknown only",
    variable=self._filter_unknown_var,
    command=self._apply_filters,
    bg="#1e1e1e", fg="#aaaaaa", selectcolor="#333333",
    activebackground="#1e1e1e", activeforeground="white",
).pack(side="left", padx=(0, 12))

self._filter_alts_var = tk.BooleanVar(value=False)
tk.Checkbutton(
    filter_bar, text="Has alts",
    variable=self._filter_alts_var,
    command=self._apply_filters,
    bg="#1e1e1e", fg="#aaaaaa", selectcolor="#333333",
    activebackground="#1e1e1e", activeforeground="white",
).pack(side="left")
```

### `_apply_filters()` method

```python
def _apply_filters(self) -> None:
    want_unknown = self._filter_unknown_var.get()
    want_alts    = self._filter_alts_var.get()
    for i, iid in enumerate(self._all_iids):
        is_unknown = i < len(self._card_ids) and self._card_ids[i] == ""
        has_alts   = i < len(self._candidate_counts) and self._candidate_counts[i] > 0
        show = True
        if want_unknown and not is_unknown:
            show = False
        if want_alts and not has_alts:
            show = False
        if show:
            self._tree.reattach(iid, "", "end")
        else:
            self._tree.detach(iid)
```

### `clear()` update
Reset both filter vars to `False` so the checkboxes reset when a new session is loaded:
```python
self._filter_unknown_var.set(False)
self._filter_alts_var.set(False)
```

---

## Summary of all file changes

| File | Changes |
|---|---|
| `ui/remap_dialog.py` | Add `<Double-ButtonRelease-1>` binding + `_on_double_click`; add `_close()` helper; save/restore geometry |
| `ui/resolution_dialog.py` | Add `_close()` helper; save/restore geometry |
| `ui/app_window.py` | Restore/save main window geometry; add `_is_on_screen()` helper (or import from roi) |
| `core/roi.py` | Add `_is_on_screen(geometry) -> bool` helper |
| `ui/log_panel.py` | Add `_all_iids`; refactor 3 index-lookup methods; add filter bar + `_apply_filters()`; reset filters in `clear()` |

---

## Verification checklist

- [ ] Double-clicking a remap candidate confirms and closes the dialog
- [ ] Single-click in remap still only selects; Confirm button still works
- [ ] Main window reopens at the same position and size as when it was closed
- [ ] Remap dialog reopens at the saved position
- [ ] Resolution (alts) dialog reopens at the saved position
- [ ] Disconnecting a monitor and relaunching: window opens at default position, not off-screen
- [ ] "Unknown only" checkbox shows only grey/unknown rows; unchecking restores all rows
- [ ] "Has alts" checkbox shows only rows with a "? N alts" flag; unchecking restores all rows
- [ ] Bulk price fetch with a filter active still updates the correct rows (not shifted indices)
- [ ] Price arriving from background fetch with a filter active updates the correct row
- [ ] `clear()` / loading a new session resets both checkboxes to unchecked
