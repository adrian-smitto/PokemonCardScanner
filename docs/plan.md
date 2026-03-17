# Build Plan

## Stack
- **Language**: Python
- **UI**: tkinter (stdlib)
- **Camera**: OpenCV
- **Card identification**: perceptual hash matching against local SQLite DB
- **Prices**: Pokemon TCG API (TCGPlayer market prices)
- **Audio**: winsound (stdlib, Windows)
- **DB**: SQLite via sqlite3 (stdlib)

---

## Project Structure

```
PokemonCardScanner/
├── build_db.py          # One-time setup: fetch all card images → compute phashes → store in SQLite
├── main.py              # Entry point
├── config.py            # All tunable constants
├── requirements.txt
├── .env                 # POKEMONTCG_API_KEY (gitignored)
│
├── core/
│   ├── camera.py        # OpenCV capture in a background daemon thread
│   ├── detector.py      # Detect card rectangle in frame (Canny + contour)
│   ├── cropper.py       # Perspective-correct the detected card quad
│   ├── hasher.py        # Perceptual hash via imagehash.phash (hash_size=16)
│   ├── matcher.py       # Full-table hamming scan against db/cards.db
│   ├── price_client.py  # GET /v2/cards/{id} from Pokemon TCG API
│   ├── audio.py         # winsound.Beep() in fire-and-forget thread
│   ├── scan_log.py      # Read/write db/scan_log.db (scan_log + scan_candidates tables)
│   └── state_machine.py # Scanning loop and state transitions
│
├── ui/
│   ├── app_window.py        # Root Tk window; owns tick loop, state machine, session stats
│   ├── feed_panel.py        # Live camera canvas with contour overlay (yellow/green border)
│   ├── result_panel.py      # Current card name / set / price display
│   ├── log_panel.py         # Scrollable ttk.Treeview; ambiguous entries are clickable
│   └── resolution_dialog.py # Modal dialog for resolving ambiguous scans (E03)
│
└── db/
    ├── cards.db             # Built by build_db.py; never written at scan time
    └── scan_log.db          # Created at runtime; persists across sessions
```

---

## Databases

### `db/cards.db` — Card hash database (built once)

```sql
CREATE TABLE cards (
    id        TEXT PRIMARY KEY,  -- e.g. "sv6-123"
    name      TEXT NOT NULL,
    set_name  TEXT NOT NULL,
    set_id    TEXT NOT NULL,
    number    TEXT NOT NULL,
    image_url TEXT NOT NULL,
    phash     TEXT NOT NULL,     -- 64-char hex, 256-bit phash (hash_size=16)
    rarity    TEXT
);
CREATE INDEX idx_phash ON cards(phash);
```

**Build process**: paginate `GET /v2/cards` (250/page) → download `smallImage` via `ThreadPoolExecutor(8)` → compute phash → batch insert. Resumable. ~15–18k cards, 20–40 min first run.

### `db/scan_log.db` — Scan history (runtime)

```sql
CREATE TABLE scan_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    scanned_at   TEXT NOT NULL,      -- ISO 8601
    card_id      TEXT NOT NULL,
    card_name    TEXT NOT NULL,
    set_name     TEXT NOT NULL,
    number       TEXT NOT NULL,
    rarity       TEXT,
    market_price REAL,               -- NULL if unavailable
    hamming_dist INTEGER NOT NULL,
    is_corrected INTEGER NOT NULL DEFAULT 0  -- 1 after manual resolution
);

CREATE TABLE scan_candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id      INTEGER NOT NULL REFERENCES scan_log(id),
    card_id      TEXT NOT NULL,
    card_name    TEXT NOT NULL,
    set_name     TEXT NOT NULL,
    number       TEXT NOT NULL,
    rarity       TEXT,
    hamming_dist INTEGER NOT NULL
    -- no price column; fetched on demand during resolution
);
```

---

## Config (`config.py`)

```python
CAMERA_INDEX = 0
CARD_AREA_MIN_FRACTION = 0.08
CARD_ASPECT_RATIO = 0.714            # 63mm / 88mm
ASPECT_RATIO_TOLERANCE = 0.12
STABILIZE_FRAMES = 8
STABILIZE_HASH_THRESHOLD = 10        # max hamming between consecutive frames
MATCH_HAMMING_THRESHOLD = 15         # max hamming for a positive DB match
DUPLICATE_COOLDOWN_SECONDS = 5.0
BEEP_FREQUENCY_SUCCESS = 1000        # Hz
BEEP_FREQUENCY_FAILURE = 600         # Hz
BEEP_DURATION_MS = 200
DB_PATH = "db/cards.db"
SCAN_LOG_PATH = "db/scan_log.db"
POKEMONTCG_BASE_URL = "https://api.pokemontcg.io/v2"
API_TIMEOUT_SECONDS = 8
UI_FEED_WIDTH = 640
UI_FEED_HEIGHT = 360
UI_TICK_MS = 33
```

---

## Scanning State Machine

```
IDLE → CARD_DETECTED → STABILIZING → HASHING → MATCHING → FETCHING_PRICE → DONE → COOLDOWN → IDLE
```

| State | Advances when | Key behaviour |
|---|---|---|
| IDLE | Card contour detected | No border overlay |
| CARD_DETECTED | — | Yellow border shown |
| STABILIZING | 8 consecutive frames with hamming < threshold | Yellow border; hashes collected |
| HASHING | Hash computed | Canonical hash = frame with min total distance to peers |
| MATCHING | DB scan complete | Full-table hamming scan; returns primary + all candidates within threshold |
| FETCHING_PRICE | API returns or times out | Async via ThreadPoolExecutor; result via queue.Queue |
| DONE | Result emitted to queue | Green border briefly; beep plays; log written |
| COOLDOWN | 5s timer OR card removed (hash drift) | Suppresses duplicate scans |

**Ambiguous match**: if MATCHING returns >1 candidate, the one with the lowest hamming distance is the primary. All candidates are passed through to be persisted. No state change — ambiguity is handled at the persistence layer, not in the state machine.

---

## Thread Model

```
Main Thread (Tkinter)
  └── _tick() every 33ms
        ├── reads CameraCapture.latest_frame
        ├── drives StateMachine.process(frame)
        └── drains result_queue → updates UI, writes scan_log

Camera Thread (daemon)
  └── tight OpenCV capture loop → latest_frame under threading.Lock

Worker ThreadPoolExecutor (max_workers=2)
  ├── hash matching job (CPU-bound)
  └── price fetch job (IO-bound) — also used for on-demand candidate price fetch

Audio Thread (fire-and-forget daemon)
  └── winsound.Beep()
```

All Tkinter widget updates happen exclusively on the main thread via `_tick()` or `.after()` callbacks. Worker threads never touch widgets directly.

---

## Ambiguous Identification (E03)

- `matcher.py` returns a `MatchResult(primary, candidates[])` where `candidates` contains every match within `MATCH_HAMMING_THRESHOLD`, ordered by hamming distance
- `scan_log.py` writes the primary to `scan_log` and all remaining candidates to `scan_candidates` (linked by `scan_id`)
- `log_panel.py` queries for candidate count per entry; renders a "? N alternatives" badge on ambiguous rows
- Clicking an ambiguous row opens `resolution_dialog.py` (modal `tk.Toplevel`):
  - Lists candidates (name, set, number, rarity, hamming dist)
  - User selects one → app fetches price on demand via `price_client.py` (same worker pool)
  - Confirm updates `scan_log` row (card fields + price + `is_corrected=1`) and refreshes the log panel row and session totals
  - Dismiss closes without changes

---

## UI Layout

```
┌─────────────────────────────────────────────────────┐
│  [Camera Feed 640×360 — contour overlay]             │
│                                    [Result Panel]    │
│                                    Card Name         │
│                                    Set / Number      │
│                                    Rarity            │
│                                    $XX.XX (mkt)      │
│                                    $X.XX–$XX.XX      │
├─────────────────────────────────────────────────────┤
│  Scan Log (ttk.Treeview, auto-scroll)                │
│  Time  | Name | Set | # | Rarity | Price | Flags    │
│  ...                                                 │
├─────────────────────────────────────────────────────┤
│  Status bar: 42 cards — $138.50 + 3 unpriced  [Export CSV] │
└─────────────────────────────────────────────────────┘
```

---

## Dependencies (`requirements.txt`)

```
opencv-python==4.10.0.84
Pillow==10.4.0
imagehash==4.3.1
requests==2.32.3
tqdm==4.66.5
python-dotenv==1.0.1
numpy==1.26.4
```

---

## Implementation Phases

### Phase 1 — Data layer
- `build_db.py`: fetch all cards, download images, compute phashes, insert into `db/cards.db`
- Validate: run against 10 manually photographed cards, confirm correct matches
- Stories: E01-US08

### Phase 2 — Core pipeline (no UI)
- `config.py`, `core/camera.py`, `core/detector.py`, `core/cropper.py`, `core/hasher.py`, `core/matcher.py`
- Wire into a throwaway terminal script; tune detection thresholds against real cards
- Stories: E02-US03, E02-US05

### Phase 3 — Scanning pipeline
- `core/state_machine.py`, `core/price_client.py`, `core/audio.py`
- Terminal script drives full scan loop: detect → hash → match → price → beep
- Stories: E01-US01, E01-US02, E01-US03, E02-US01, E02-US02, E03-US01, E03-US02

### Phase 4 — UI
- `ui/feed_panel.py` — live camera + overlay
- `ui/result_panel.py` — card result display
- `ui/log_panel.py` — scrollable log with ambiguity badges
- `ui/app_window.py` — wires everything; tick loop; startup checks; session stats
- Stories: E01-US04, E01-US07, E02-US04, E03-US03

### Phase 5 — Persistence & resolution
- `core/scan_log.py` — `scan_log` + `scan_candidates` tables; CSV export
- `ui/resolution_dialog.py` — ambiguous scan resolution modal
- Stories: E01-US05, E01-US06, E03-US04

---

## Verification Checklist

- [ ] `python build_db.py` completes; `db/cards.db` has ~15k rows
- [ ] Core pipeline terminal script matches 10 test cards correctly
- [ ] `python main.py` opens, camera feed live within 2s
- [ ] Missing `db/cards.db` shows setup message, does not crash
- [ ] Missing `.env` / no API key: scanning works, prices show "N/A"
- [ ] Placing a card: result + green border flash + beep + log entry
- [ ] Holding card still 10s: only one log entry
- [ ] Fast card swap: each card logged separately
- [ ] Unrecognised card: "Card not recognized" + failure beep + no log entry
- [ ] Price API failure: card logged with "N/A", success beep still plays
- [ ] Ambiguous scan: "? N alternatives" badge in log row
- [ ] Clicking ambiguous entry: resolution dialog opens with candidate list
- [ ] Selecting candidate + confirm: price fetched, log row updated, totals updated
- [ ] Dismiss dialog: no changes made
- [ ] Export CSV: file contains all sessions; corrected scans reflect resolved card
