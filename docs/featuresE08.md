# Features E08 — Manual Screen Capture

Covers stories: E08-US01, E08-US02.

---

## Design summary

The user presses `Ctrl+Shift+S` (configurable) while the app has focus and scanning is
disabled. A fullscreen semi-transparent Tkinter overlay appears. The user drags a
rectangle around a card image visible on screen (browser, spreadsheet, anywhere). On
mouse release, `PIL.ImageGrab.grab(bbox)` captures the region; the image is saved and
run through the same phash → `find_matches` pipeline as a camera scan. The result is
appended to the log panel. The overlay closes; the user presses the shortcut again for
the next card.

No new pip dependencies. `PIL.ImageGrab` is already available via Pillow. The overlay is
pure Tkinter.

---

## Implementation order

```
1. config.py          — add SNIP_HOTKEY
2. ui/snip_overlay.py — new: fullscreen overlay + rubber-band selection
3. ui/app_window.py   — bind shortcut; drain snip futures; wire result into existing pipeline
```

---

## Feature 1 — `config.py`

```python
SNIP_HOTKEY = "<Control-Shift-S>"   # Tkinter key binding syntax
```

---

## Feature 2 — `ui/snip_overlay.py` (new file)

### Responsibilities
- Cover the full primary screen with a semi-transparent, borderless Toplevel
- Capture mouse drag to draw a rubber-band selection rectangle on a Canvas
- On release: deliver the screen-coordinate bbox `(x1, y1, x2, y2)` to the caller
- On Escape: close without calling back

### Class interface

```python
class SnipOverlay:
    def __init__(self, parent: tk.Tk, on_capture: callable, on_cancel: callable = None):
        ...
```

`on_capture(bbox: tuple[int,int,int,int])` — called with screen coordinates on release.
`on_cancel()` — called when Escape is pressed (optional).

### Window setup

```python
self._win = tk.Toplevel(parent)
self._win.attributes('-fullscreen', True)
self._win.attributes('-alpha', 0.25)
self._win.attributes('-topmost', True)
self._win.overrideredirect(True)      # no title bar / borders
self._win.configure(bg='grey')
self._win.grab_set()
```

### Canvas + bindings

```python
self._canvas = tk.Canvas(self._win, cursor='crosshair',
                         bg='grey', highlightthickness=0)
self._canvas.pack(fill='both', expand=True)

self._start: tuple[int,int] | None = None
self._rect_id = None

self._canvas.bind('<ButtonPress-1>',   self._on_press)
self._canvas.bind('<B1-Motion>',       self._on_drag)
self._canvas.bind('<ButtonRelease-1>', self._on_release)
self._win.bind('<Escape>',             self._on_escape)
```

### Coordinate strategy

Mouse events on the Canvas give widget-local `(event.x, event.y)`. Because the window is
fullscreen and positioned at (0, 0), widget-local coordinates equal screen coordinates on
the primary monitor. Use them directly as the `bbox` for `ImageGrab.grab()`.

```python
def _on_press(self, event) -> None:
    self._start = (event.x, event.y)
    if self._rect_id:
        self._canvas.delete(self._rect_id)

def _on_drag(self, event) -> None:
    if not self._start:
        return
    if self._rect_id:
        self._canvas.delete(self._rect_id)
    self._rect_id = self._canvas.create_rectangle(
        *self._start, event.x, event.y,
        outline='red', width=2, fill='',
    )

def _on_release(self, event) -> None:
    if not self._start:
        return
    x1, y1 = self._start
    x2, y2 = event.x, event.y
    # Normalise so x1<x2, y1<y2
    bbox = (min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2))
    self._win.destroy()
    if bbox[2] - bbox[0] > 5 and bbox[3] - bbox[1] > 5:
        self._on_capture(bbox)
    # else: selection too small — silently discard

def _on_escape(self, _event) -> None:
    self._win.destroy()
    if self._on_cancel:
        self._on_cancel()
```

---

## Feature 3 — `ui/app_window.py`

### New imports

```python
from PIL import ImageGrab
import config          # already imported
from ui.snip_overlay import SnipOverlay
```

### New instance variables (in `__init__`)

```python
self._snip_futures: list = []   # (future, scan_token)
```

### Shortcut binding (in `_startup`, after the root window exists)

```python
self._root.bind(config.SNIP_HOTKEY, self._on_snip_hotkey)
```

### Snip label in status bar (in `_build_ui`, `info_row`)

```python
self._snip_var = tk.StringVar(value=f"Snip: {config.SNIP_HOTKEY.strip('<>').replace('-', '+')} (disabled while scanning)")
self._snip_label = tk.Label(
    info_row, textvariable=self._snip_var,
    fg="#555555", bg="#1e1e1e", font=("Helvetica", 8),
)
self._snip_label.pack(side="right", padx=(0, 10))
```

Update label colour in `_toggle_scanning`:
```python
self._snip_label.config(fg="#555555" if self._scanning else "#888888")
```

### `_on_snip_hotkey`

```python
def _on_snip_hotkey(self, _event=None) -> None:
    if self._scanning or self._state_machine is None:
        return
    self._snip_var.set("Snipping…")
    self._snip_label.config(fg="#ffcc00")
    SnipOverlay(
        self._root,
        on_capture=self._on_snip_capture,
        on_cancel=self._on_snip_cancel,
    )
```

### `_on_snip_cancel`

```python
def _on_snip_cancel(self) -> None:
    self._snip_var.set(f"Snip: Ctrl+Shift+S")
    self._snip_label.config(fg="#888888")
```

### `_on_snip_capture`

```python
def _on_snip_capture(self, bbox: tuple) -> None:
    self._snip_var.set(f"Snip: Ctrl+Shift+S")
    self._snip_label.config(fg="#888888")

    try:
        img = ImageGrab.grab(bbox=bbox, all_screens=False)
        img = img.convert("RGB")
    except Exception as e:
        self._debug.log(f"Screen capture failed: {e}", "error")
        return

    scan_token = str(uuid.uuid4())

    # Save capture image (same as camera pipeline)
    os.makedirs(config.CAPTURES_DIR, exist_ok=True)
    try:
        img.save(os.path.join(config.CAPTURES_DIR, f"{scan_token}.jpg"))
    except Exception:
        pass

    # Submit match to background executor
    matcher = self._state_machine.matcher
    future = self._executor.submit(
        self._run_snip_match, img, matcher
    )
    self._snip_futures.append((future, scan_token))
```

### `_run_snip_match` (runs in executor thread)

```python
@staticmethod
def _run_snip_match(img, matcher):
    from core.hasher import compute_phash
    query_hash = compute_phash(img)
    hash_180 = compute_phash(img.rotate(180))
    return matcher.find_matches(query_hash, hash_180)
```

### `_drain_snip_futures` (called from `_tick`)

```python
def _drain_snip_futures(self) -> None:
    still_pending = []
    for future, scan_token in self._snip_futures:
        if not future.done():
            still_pending.append((future, scan_token))
            continue
        try:
            result = future.result()
        except Exception:
            result = None
        self._handle_snip_result(result, scan_token)
    self._snip_futures = still_pending
```

Add `self._drain_snip_futures()` call in `_tick`, alongside `_drain_manual_prices()`.

### `_handle_snip_result`

Reuses the existing `_handle_result` path almost entirely. The only difference is the
`ScanResult` is built here rather than coming from the state machine queue.

```python
def _handle_snip_result(self, match_result, scan_token: str) -> None:
    from core.state_machine import ScanResult
    from core.matcher import MatchResult

    if match_result is None:
        closest = self._state_machine.matcher.last_closest_dist
        self._debug.log(
            f"Snip: no match (closest dist={closest}) — logged as Unknown", "error"
        )
        audio.play_failure()
        result = ScanResult(
            scan_token=scan_token,
            session_id=self._session_id,
            card_id="", card_name="Unknown",
            set_name="", number="", rarity=None,
            market_price=None, low_price=None, high_price=None,
            hamming_dist=closest if closest is not None else -1,
            price_error=None, candidates=[],
        )
    else:
        primary = match_result.primary
        self._debug.log(
            f"Snip matched: {primary.name} [{primary.set_name} #{primary.number}]"
            f" dist={primary.hamming_dist}", "match"
        )
        audio.play_card_scanned()
        candidates = [
            {"card_id": c.card_id, "card_name": c.name, "set_name": c.set_name,
             "number": c.number, "rarity": c.rarity, "hamming_dist": c.hamming_dist}
            for c in match_result.candidates
        ]
        result = ScanResult(
            scan_token=scan_token,
            session_id=self._session_id,
            card_id=primary.card_id,
            card_name=primary.name,
            set_name=primary.set_name,
            number=primary.number,
            rarity=primary.rarity,
            market_price=None, low_price=None, high_price=None,
            hamming_dist=primary.hamming_dist,
            price_error=None, candidates=candidates,
        )

    self._handle_result(result)   # existing method handles DB log + panel + price fetch
```

---

## Summary of all file changes

| File | Changes |
|---|---|
| `config.py` | Add `SNIP_HOTKEY` |
| `ui/snip_overlay.py` | **New file** — fullscreen overlay + rubber-band selection |
| `ui/app_window.py` | Bind shortcut; `_on_snip_hotkey`, `_on_snip_capture`, `_on_snip_cancel`, `_run_snip_match`, `_drain_snip_futures`, `_handle_snip_result`; snip label in status bar; drain call in `_tick` |

No changes to `core/`, `core/state_machine.py`, `ui/log_panel.py`, or the DB schema.

---

## Limitations / known constraints

- **Primary monitor only**: `attributes('-fullscreen', True)` covers only the primary
  monitor. The snip area is limited to that monitor. Multi-monitor support would require
  computing the virtual desktop bounds — not in scope for this story.
- **App must have focus**: The shortcut is a Tkinter root binding, not a global OS hook.
  The user must click the app once before pressing the shortcut. In practice this is fine
  because the app window can sit alongside the browser.
- **Scanning must be disabled**: The shortcut silently no-ops if scanning is on.
  This is intentional — the two modes are mutually exclusive.

---

## Verification checklist

- [ ] `Ctrl+Shift+S` with scanning disabled opens the semi-transparent overlay
- [ ] `Ctrl+Shift+S` with scanning enabled does nothing
- [ ] Crosshair cursor appears; dragging draws a red selection rectangle
- [ ] Escape closes the overlay without logging anything
- [ ] A very small drag (< 5px) is silently discarded without crashing
- [ ] After release, the overlay closes immediately
- [ ] Matched snip appears in the log panel with correct name/set/number; price fetches in background
- [ ] Unmatched snip appears as "Unknown" in grey; remap works (capture image exists)
- [ ] Capture image is saved in `captures/` with the correct scan_token filename
- [ ] Snipped rows appear in CSV and JSON exports
- [ ] Pressing the shortcut again after a capture opens a fresh overlay
- [ ] Snip label in status bar shows "Snipping…" while overlay is open, reverts after
- [ ] Snip label is greyed out when scanning is active
