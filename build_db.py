"""
One-time setup script.
Downloads all Pokemon card images from the Pokemon TCG API,
computes perceptual hashes, and stores them in db/cards.db.

Usage:
    python build_db.py               # normal run (resumes from cache)
    python build_db.py --clear-cache # wipe all cached pages and re-fetch everything

Requires POKEMONTCG_API_KEY in .env (or set as environment variable).
Re-running is safe — already-processed cards are skipped.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image
from tqdm import tqdm

from dotenv import load_dotenv
import imagehash

load_dotenv()

DB_PATH = "db/cards.db"
API_BASE = "https://api.pokemontcg.io/v2"
PAGE_SIZE = 250
WORKERS = 8
BATCH_SIZE = 100
HASH_SIZE = 16

PAGE_CACHE_DIR = "db/page_cache"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cards (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    set_name    TEXT NOT NULL,
    set_id      TEXT NOT NULL,
    number      TEXT NOT NULL,
    image_url   TEXT NOT NULL,
    local_image TEXT,
    phash       TEXT NOT NULL,
    rarity      TEXT
);
"""
_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_phash ON cards(phash);"


def get_session(api_key: str) -> requests.Session:
    session = requests.Session()
    if api_key:
        session.headers["X-Api-Key"] = api_key
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _page_cache_path(page: int) -> str:
    return os.path.join(PAGE_CACHE_DIR, f"page_{page:04d}.json")


def _load_cached_page(page: int) -> list[dict] | None:
    path = _page_cache_path(page)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_cached_page(page: int, batch: list[dict]) -> None:
    os.makedirs(PAGE_CACHE_DIR, exist_ok=True)
    with open(_page_cache_path(page), "w", encoding="utf-8") as f:
        json.dump(batch, f)


def clear_cache() -> None:
    if os.path.exists(PAGE_CACHE_DIR):
        shutil.rmtree(PAGE_CACHE_DIR)
        print(f"Cleared page cache ({PAGE_CACHE_DIR})")
    else:
        print("No cache to clear.")


def _get_with_retry(session: requests.Session, url: str, params: dict = None,
                    retries: int = 5, timeout: int = 60) -> requests.Response | None:
    """GET with manual retry on timeout/connection errors."""
    for attempt in range(retries):
        try:
            return session.get(url, params=params, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            wait = 2 ** attempt
            print(f"\n  Timeout on attempt {attempt + 1}/{retries}, retrying in {wait}s...")
            time.sleep(wait)
    return None


def fetch_all_cards(session: requests.Session) -> list[dict]:
    print("Fetching card list from Pokemon TCG API...")
    cards = []
    page = 1

    while True:
        cached = _load_cached_page(page)
        if cached is not None:
            cards.extend(cached)
            print(f"  Page {page}: {len(cards)} cards (from cache)", end="\r")
            if len(cached) < PAGE_SIZE:
                break
            page += 1
            continue

        resp = _get_with_retry(session, f"{API_BASE}/cards",
                               params={"page": page, "pageSize": PAGE_SIZE})
        if resp is None:
            print(f"\nPage {page} failed after retries. Run again to resume.")
            sys.exit(1)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("data", [])
        if not batch:
            break

        _save_cached_page(page, batch)
        cards.extend(batch)
        total = data.get("totalCount", "?")
        print(f"  Page {page}: {len(cards)} / {total} cards fetched", end="\r")

        if len(batch) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.1)

    print(f"\nTotal cards: {len(cards)}")
    return cards


IMAGE_CACHE_DIR = "db/images"


def _image_path(card_id: str) -> str:
    """Local path for a card image. Uses card ID with / replaced to avoid subdirs."""
    safe_id = card_id.replace("/", "_")
    return os.path.join(IMAGE_CACHE_DIR, f"{safe_id}.jpg")


def download_and_hash(card: dict, session: requests.Session) -> dict | None:
    """Download small card image, save to disk, compute phash. Returns None on failure."""
    import io
    try:
        image_url = card.get("images", {}).get("small")
        if not image_url:
            return None

        local_path = _image_path(card["id"])

        # Use cached image if already downloaded
        if os.path.exists(local_path):
            img = Image.open(local_path).convert("RGB")
        else:
            resp = session.get(image_url, timeout=15)
            resp.raise_for_status()
            os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            img.save(local_path, "JPEG", quality=85)

        h = imagehash.phash(img, hash_size=HASH_SIZE)

        return {
            "id": card["id"],
            "name": card["name"],
            "set_name": card.get("set", {}).get("name", ""),
            "set_id": card.get("set", {}).get("id", ""),
            "number": card.get("number", ""),
            "image_url": image_url,
            "local_image": local_path,
            "phash": str(h),
            "rarity": card.get("rarity"),
        }
    except Exception:
        return None


def build_database(api_key: str) -> None:
    os.makedirs("db", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_INDEX)
    conn.commit()

    session = get_session(api_key)
    all_cards = fetch_all_cards(session)

    existing = {row[0] for row in conn.execute("SELECT id FROM cards").fetchall()}
    to_process = [c for c in all_cards if c["id"] not in existing]

    print(f"\n{len(existing)} already in DB, {len(to_process)} to process.")

    if not to_process:
        print("Database is up to date.")
        conn.close()
        return

    processed = 0
    failed = 0
    batch = []

    with tqdm(total=len(to_process), unit="card", desc="Building DB") as pbar:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(download_and_hash, c, session): c for c in to_process}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    batch.append(result)
                    processed += 1
                else:
                    failed += 1
                pbar.update(1)

                if len(batch) >= BATCH_SIZE:
                    _insert_batch(conn, batch)
                    batch = []

    if batch:
        _insert_batch(conn, batch)

    conn.close()
    print(f"\nDone. Processed: {processed}  Skipped: {len(existing)}  Failed: {failed}")


def _insert_batch(conn: sqlite3.Connection, batch: list[dict]) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO cards
           (id, name, set_name, set_id, number, image_url, local_image, phash, rarity)
           VALUES (:id, :name, :set_name, :set_id, :number, :image_url, :local_image, :phash, :rarity)""",
        batch,
    )
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the Pokemon card hash database.")
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear all cached API pages and re-fetch from scratch. Use when a new card set has been released.",
    )
    args = parser.parse_args()

    if args.clear_cache:
        clear_cache()

    api_key = os.getenv("POKEMONTCG_API_KEY", "")
    if not api_key:
        print("Warning: POKEMONTCG_API_KEY not set. Requests will be rate-limited.")
        print("Add it to a .env file: POKEMONTCG_API_KEY=your_key_here\n")

    try:
        build_database(api_key)
    except KeyboardInterrupt:
        print("\nInterrupted. Run again to resume — cached pages and DB progress are preserved.")
        sys.exit(0)
