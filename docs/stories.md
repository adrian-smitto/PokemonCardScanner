# User Stories

## E01 — Card Scanning Workflow

### E01-US01 — Scan a card and get its price
**As a** collector pricing my collection,
**I want** to place a Pokemon card under the camera and have the app automatically identify it and show the current market price,
**So that** I don't have to manually look up each card.

**Acceptance criteria:**
- App detects the card without any button press
- Card name, set, set number, and rarity are displayed once identified
- TCGPlayer market price is displayed as the primary value; low and high prices are shown as secondary
- Result appears within ~2 seconds of the card reaching stable lock-in
- A success beep sounds when the result is ready
- If the card is removed before the scan completes, the app returns to idle without logging anything
- UI shows a neutral "Waiting for card..." state when no card is detected

---

### E01-US02 — Audio signal to place the next card
**As a** collector scanning cards quickly,
**I want** to hear distinct audio feedback for success and failure,
**So that** I can keep my eyes on the card stack instead of the screen.

**Acceptance criteria:**
- Success: a single beep at 1000 Hz for 200ms plays when a card is identified
- Failure: two short beeps at 600 Hz play when a card cannot be identified
- No beep plays while a scan is still in progress
- Audio runs in a background thread and does not block the UI or next scan

---

### E01-US03 — Avoid duplicate scans
**As a** collector,
**I want** the app to not log the same card twice just because it sat still on the surface,
**So that** my log is accurate.

**Acceptance criteria:**
- Once a card is scanned and logged, holding it still does not produce additional log entries
- A 5-second cooldown begins after each successful scan; the same card cannot be logged again within that window
- If the card is removed during cooldown, the cooldown resets immediately and the app is ready for the next card
- Removing a card and placing it again after the cooldown produces a new log entry
- Fast card swaps (remove, place next) each produce their own log entry as long as the new card reaches stable lock-in

---

### E01-US04 — Browse the scan log during a session
**As a** collector,
**I want** to see a scrollable list of all cards I've scanned in the current session with their prices,
**So that** I can review what I've processed without leaving the scanning view.

**Acceptance criteria:**
- Each log entry shows: timestamp, card name, set, number, rarity, and market price
- If price is unavailable, the price column shows "N/A"
- Log auto-scrolls to the most recent entry after each scan
- Log is visible alongside the live camera feed (no screen switching)
- When no scans have been made yet, the log shows an empty state message ("No cards scanned yet")
- Log entries are display-only except for ambiguous entries (those with saved candidates), which are clickable to open the resolution view (E03-US04)

---

### E01-US05 — Scan log persists between sessions
**As a** collector spanning multiple sessions,
**I want** my scan history saved to disk,
**So that** I can review or export past results later.

**Acceptance criteria:**
- Every scan is written to `db/scan_log.db` in real time (not batched)
- The DB contains two tables: `scan_log` (one row per scan) and `scan_candidates` (one row per alternative candidate, linked by scan ID)
- Each `scan_log` record includes: timestamp, card ID, card name, set, number, rarity, market price, hamming distance, is_corrected flag, and session ID
- Each `scan_candidates` record includes: scan ID (foreign key), card ID, name, set, number, rarity, and hamming distance
- The UI log on reopen shows only the current session's scans; previous sessions remain in the DB only
- If `db/scan_log.db` is missing on startup, the app creates it automatically
- If the DB file is corrupted on startup, the app logs an error and creates a fresh DB without crashing

---

### E01-US06 — Export scan log to CSV
**As a** collector,
**I want** to export my scan history to a CSV file,
**So that** I can open it in a spreadsheet for further analysis or record keeping.

**Acceptance criteria:**
- An "Export CSV" button is available in the UI
- Exported file includes all scans from all sessions with columns: timestamp, card name, set, number, rarity, market price, session ID, is_corrected
- If a scan was manually corrected (E03-US04), the exported row reflects the corrected card and price, not the original auto-selected one
- File is saved to a location chosen by the user via a save dialog
- Export works even if no scans have been made (produces a header-only CSV)

---

### E01-US07 — Session summary
**As a** collector finishing a session,
**I want** to see a running total of cards scanned and their combined market value,
**So that** I have an at-a-glance summary without counting manually.

**Acceptance criteria:**
- A status bar shows: total cards scanned this session and total combined market value
- Totals update immediately after each successful scan
- Cards with unavailable prices contribute 0 to the total value and are counted separately (e.g., "42 cards — $138.50 + 3 unpriced")

---

### E01-US08 — Build the card hash database
**As a** developer / first-time setup user,
**I want** to run a one-time script that downloads all Pokemon card images and builds the local identification database,
**So that** the app can identify cards offline at scan time.

**Acceptance criteria:**
- `python build_db.py` runs without errors given a valid API key
- API key is read from a `.env` file (`POKEMONTCG_API_KEY`); if missing or invalid, the script exits with a clear error message before downloading anything
- `db/cards.db` is populated with ~15,000–18,000 card records, each with a phash, name, set, number, rarity, and image URL
- Progress is shown as a count and percentage (e.g., "12,400 / 15,200 cards — 81%")
- Script is resumable — re-running skips already-processed cards
- Script prints a completion summary: total cards processed, skipped, and failed

---

### E01-US09 — Unique scan ID and capture image per scan
**As a** collector,
**I want** every scan to have a unique ID that ties together the log entry and the captured image,
**So that** I can later retrieve exactly what the camera saw for any logged scan (e.g., for manual remapping).

**Acceptance criteria:**
- A UUID (`scan_token`) is generated at scan time and persisted in the `scan_log` table alongside the other scan fields
- At scan time, the cropped card image is saved to `db/captures/{scan_token}.jpg`
- The capture image is saved regardless of whether the match succeeded or not
- The `scan_token` column is added to the existing `scan_log` schema; existing rows without a token are left as-is (nullable)
- *Prerequisite for E04-US02 (manual remap)*

---

## E02 — Reliability & Accuracy

### E02-US01 — Handle unrecognized cards gracefully
**As a** collector,
**I want** the app to clearly indicate when it cannot identify a card,
**So that** I know to handle that card manually.

**Acceptance criteria:**
- If no hash match is found above the confidence threshold, the result panel shows "Card not recognized" in a visually distinct error style
- The failure beep plays (two short beeps)
- No entry is added to the scan log
- After showing "not recognized", the app waits for the card to be removed before returning to idle (does not loop-retry on the same card)

---

### E02-US02 — Handle price API failures gracefully
**As a** collector,
**I want** the scan to still be logged even if the price lookup fails,
**So that** I don't lose the identification result because of a network hiccup.

**Acceptance criteria:**
- The price fetch is attempted once; if it times out or errors, it is not retried automatically
- The card is still identified, beeped, and logged with price shown as "N/A"
- The result panel shows a small note (e.g., "Price unavailable — API error") alongside the card details
- The success beep still plays (card was identified)

---

### E02-US03 — Stable detection before scanning
**As a** collector,
**I want** the app to wait until the card is held still before scanning,
**So that** I don't get false or blurry results while placing the card.

**Acceptance criteria:**
- App requires 8 consecutive stable frames before triggering a scan (configurable in `config.py`)
- While a card is detected but not yet stable, the camera overlay shows a yellow border
- When the card is stable and a scan is about to trigger, the overlay turns green
- When no card is detected, no border is shown
- A card that is still being placed or adjusted does not trigger a scan

---

### E02-US04 — App startup
**As a** user launching the app,
**I want** the app to open and be ready to scan with minimal setup,
**So that** I can start pricing cards immediately.

**Acceptance criteria:**
- `python main.py` opens the main window
- Camera feed is live within 2 seconds of launch
- If `db/cards.db` does not exist, the app shows a clear setup message ("Run build_db.py first") and does not crash
- If the camera cannot be opened (wrong index, disconnected), the app shows an error message and does not crash
- App reads `POKEMONTCG_API_KEY` from `.env` on startup; if missing, price lookups show "N/A" but scanning still works

---

### E02-US05 — Camera selection
**As a** user with multiple cameras connected,
**I want** to configure which camera the app uses,
**So that** the correct camera (pointed at the scanning surface) is used.

**Acceptance criteria:**
- Camera index is configurable via `CAMERA_INDEX` in `config.py`
- If the configured camera index cannot be opened, the app shows an error identifying the problem (e.g., "Camera 1 not found — check CAMERA_INDEX in config.py")
- No in-app UI for camera switching in this version (config file only)

---

### E02-US06 — Digital zoom
**As a** collector setting up the scanning surface,
**I want** to zoom into the camera feed in software,
**So that** I can frame the card precisely without moving the camera.

**Acceptance criteria:**
- "+" and "−" zoom buttons are visible in the UI, plus a zoom level label (e.g., "1.0×")
- Zoom range: 1.0× (no zoom) to 4.0×, in 0.25× steps
- Digital zoom crops the centre of the frame and scales it up to fill the feed panel
- Card detection and perspective correction run on the zoomed (cropped) image, not the raw frame
- Zoom level persists for the session but resets to 1.0× on restart

---

### E02-US07 — Camera native zoom
**As a** collector with a camera that supports hardware zoom,
**I want** the app to apply native zoom via the camera driver,
**So that** I get better image quality than digital zoom alone.

**Acceptance criteria:**
- On startup, the app attempts to set `cv2.CAP_PROP_ZOOM` to the configured value
- Native zoom level is configurable via `CAMERA_NATIVE_ZOOM` in `config.py` (default: 0, meaning no change)
- If the camera does not support native zoom, the app silently ignores it and continues
- Native zoom and digital zoom are independent and can be used together

---

### E02-US08 — Define scannable area
**As a** collector setting up the scanning surface,
**I want** to draw a region on the camera feed that marks where cards will be placed,
**So that** the app only scans inside that area and ignores everything outside it.

**Acceptance criteria:**
- User clicks and drags on the camera feed to draw a rectangle defining the scannable area
- While dragging, a live rectangle is drawn on the feed showing the region being defined
- Once released, the ROI is set and shown as a persistent coloured border overlay on the feed
- Card detection runs only on the pixels inside the ROI; the rest of the frame is ignored
- A "Clear ROI" button resets the scannable area back to the full frame
- The ROI is saved to a local `settings.json` file and restored on next launch
- ROI coordinates are stored relative to the raw frame resolution so they survive zoom changes

---

### E02-US10 — 180° rotation fallback for fixed-slot scanning
**As a** collector using a card slot that accepts cards in either orientation,
**I want** the app to automatically try the card rotated 180° if the first match attempt fails,
**So that** I don't have to worry about which way the card faces when I drop it in.

**Acceptance criteria:**
- If the initial hash match returns no result within the threshold, the crop is rotated 180° and matched again
- If the rotated match succeeds, it is used as the result — no extra log noise or warning
- If neither orientation matches, the normal "no match" failure behaviour applies
- The rotation attempt adds no perceptible delay (hash comparison is fast enough in-memory)

---

### E02-US09 — Visual feedback inside the scannable area
**As a** collector using a defined scannable area,
**I want** the ROI overlay to change appearance when a card is detected inside it,
**So that** I have clear visual confirmation that the card is in the right place.

**Acceptance criteria:**
- When no card is detected: ROI border is shown in dim white
- When a card is detected but not yet stable: ROI border turns yellow
- When the card is stable and locked in: ROI border turns green
- When no ROI is set, the existing full-frame contour overlay behaviour applies (unchanged)

---

## E03 — Ambiguous Card Identification

### E03-US01 — Auto-select the best match when multiple candidates exist
**As a** collector scanning cards,
**I want** the app to automatically pick the most likely match when multiple cards look similar,
**So that** scanning is never blocked waiting for my input.

**Acceptance criteria:**
- When multiple hash matches fall within the confidence threshold, the one with the lowest hamming distance is selected as the primary result
- The scan proceeds immediately using the primary result — no user input required
- If two or more candidates share the same lowest hamming distance, the first one returned from the DB is used as the primary result
- The primary result is displayed and logged exactly as a normal unambiguous scan

---

### E03-US02 — Save all candidate matches alongside the primary result
**As a** collector reviewing my log,
**I want** all possible matches for a scan to be saved, not just the one the app picked,
**So that** I have the information I need to correct it later if the auto-selection was wrong.

**Acceptance criteria:**
- All candidate matches within the confidence threshold are saved to the scan log alongside the primary result
- Each candidate record includes: card ID, name, set, number, rarity, and hamming distance
- Candidates are ordered by hamming distance (closest first)
- The primary result is flagged as `is_primary = true`; all others as `is_primary = false`
- A scan with no ambiguity (single match) stores only the primary result with no candidates

---

### E03-US03 — Indicate ambiguous scans in the log
**As a** collector reviewing my log,
**I want** to see at a glance which scans had multiple possible matches,
**So that** I know which entries may need manual correction.

**Acceptance criteria:**
- Log entries with saved candidates show a visual indicator (e.g., a "?" badge or "2 alternatives" label)
- Log entries with a single unambiguous match show no indicator
- Entries that have been manually resolved (E03-US04) show a "corrected" indicator instead

---

### E03-US04 — Manually resolve an ambiguous scan
**As a** collector,
**I want** to click an ambiguous log entry and pick the correct card from the list of candidates,
**So that** my log reflects the true identity of that card.

**Acceptance criteria:**
- Clicking an ambiguous log entry opens a detail view listing all saved candidates with their name, set, number, rarity, and hamming distance
- Candidate prices are not shown upfront; when the user selects a candidate, the app fetches its price from the Pokemon TCG API on demand before confirming
- While the price is being fetched, the confirm button shows a loading state
- The user can confirm once the price is returned (or if the fetch fails, confirms with price as "N/A")
- On confirmation, the log entry's primary result is updated to the selected candidate, its price is recorded, and the entry is marked as "corrected"
- The session total value updates to reflect the corrected price
- The user can dismiss the detail view without making a change

---

## E04 — Manual Operations & Data Quality

### E04-US01 — Store card reference images locally
**As a** developer / first-time setup user,
**I want** `build_db.py` to download and store the reference image for each card,
**So that** the app can display card thumbnails in the manual remap dialog without a network request.

**Acceptance criteria:**
- `build_db.py` downloads the small image for each card and saves it to `db/images/{card_id}.png`
- Downloads run in parallel (thread pool) alongside hash computation; overall build time should not increase significantly
- Script is resumable — cards whose image file already exists on disk are skipped
- If an individual image download fails, the card record is still written with a null or empty `image_path`; the failure is counted in the completion summary
- *Prerequisite for E04-US02 (manual remap thumbnails)*

---

### E04-US02 — Right-click → Get Price
**As a** collector,
**I want** to right-click any scan log row and request a fresh price fetch,
**So that** I can recover a price for entries where the initial fetch failed or timed out.

**Acceptance criteria:**
- Right-clicking a log row shows a context menu with a "Get Price" option
- Selecting it triggers a background price fetch for that row's card ID
- While the fetch is in progress, the price column shows a loading indicator (e.g., "…")
- On success, the price column updates with the new value and the DB record is updated
- On failure, the price column reverts to "N/A" and the error is logged to the debug log
- "Get Price" is available on all rows regardless of whether a price is already present (allows refresh)

---

### E04-US03 — Right-click → Manual Remap
**As a** collector,
**I want** to right-click a scan log row and manually pick the correct card from a list of close matches,
**So that** I can correct misidentified scans using the actual captured image as a reference.

**Acceptance criteria:**
- Right-clicking a log row shows a context menu with a "Remap Card" option
- Selecting it opens a remap dialog showing:
  - The captured crop image saved at scan time (from E01-US09), identified by `scan_token`
  - A scrollable list of the top N closest DB matches, each showing: card thumbnail (from E04-US01), card name, set, number, rarity, and hamming distance
  - N is configurable in-app (default 100) via a control visible in the dialog or settings
- The user selects the correct card from the list and confirms
- On confirmation, the log row is updated with the selected card's details and marked as "corrected"
- A price fetch is triggered immediately after remapping; the price column updates when it returns
- The user can dismiss the dialog without making a change
- *Depends on E01-US09 (capture images) and E04-US01 (card reference images)*
