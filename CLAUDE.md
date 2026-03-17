# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A desktop Pokemon card scanner that uses a camera pointed at a fixed surface to identify cards and retrieve their prices quickly. Target use case: pricing large collections (1000+ cards) efficiently. See `docs/vision.md` and `docs/plan.md` for full details.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# First-time setup: build the card hash database (~15k cards, 20-40 min)
python build_db.py

# Run the app
python main.py
```

## Architecture

See `docs/plan.md` for the full build plan. Key points:

- **`config.py`** — all tunable constants (camera index, detection thresholds, API key, paths)
- **`build_db.py`** — one-time script that populates `db/cards.db` with perceptual hashes of all Pokemon cards
- **`core/state_machine.py`** — central scanning loop; drives all other core modules; states: `IDLE → CARD_DETECTED → STABILIZING → MATCHING → FETCHING_PRICE → DONE → COOLDOWN`
- **`core/`** — camera capture, card detection (OpenCV Canny+contour), perspective correction, phash computation, SQLite hash matching, TCG API price client, audio, scan log persistence
- **`ui/`** — tkinter window wired to the state machine via a 33ms tick loop; all widget updates on the main thread only; worker threads communicate via `queue.Queue`

## Key design rules

- Tkinter is not thread-safe — never update widgets from worker threads; use `result_queue` + `_tick()` only
- `db/cards.db` is read-only at scan time; only `build_db.py` writes to it
- `db/scan_log.db` has two tables: `scan_log` and `scan_candidates` (for ambiguous matches)
- API key is optional — scanning works without it but prices show "N/A"
