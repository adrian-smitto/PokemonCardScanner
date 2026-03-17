# Vision

Desktop PC Pokemon card scanner using a camera to get card prices fast.

# Problem it solves
Having 1000+ cards and needing to price them quickly without manual lookups.

# How it solves it
Camera is pointed at a fixed surface. User places a card, the app automatically identifies it, fetches the current market price, plays an audio beep to signal completion, and the user places the next card. Repeat until the collection is priced.

# Features
1. Card identification and pricing via camera
2. Audio beep notification when a scan is complete (signals user to place next card)
3. Full scrollable log of all identified cards for the session, with prices

# Tech Stack
- **Language**: Python
- **Card identification**: Perceptual image hash matching (imagehash phash, 256-bit) against a local SQLite database built from Pokemon TCG API card images
- **Price source**: Pokemon TCG API (api.pokemontcg.io) — returns TCGPlayer market prices
- **UI**: tkinter desktop window — live camera feed, current card result, scrollable log
- **Camera**: OpenCV
- **Audio**: winsound (Windows stdlib)

# Architecture
See `docs/architecture.md` for the full build-ready plan.