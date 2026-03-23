import sqlite3
import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
import config


@dataclass
class ScanRecord:
    session_id: str
    card_id: str
    card_name: str
    set_name: str
    number: str
    rarity: str | None
    market_price: float | None
    hamming_dist: int
    candidates: list[dict] = field(default_factory=list)
    scanned_at: str = ""
    id: int | None = None
    is_corrected: bool = False
    scan_token: str | None = None
    holo_type: str | None = None
    holo_variants: list = None

    def __post_init__(self):
        if not self.scanned_at:
            self.scanned_at = datetime.now(timezone.utc).isoformat()


_CREATE_SCAN_LOG = """
CREATE TABLE IF NOT EXISTS scan_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    scanned_at   TEXT NOT NULL,
    card_id      TEXT NOT NULL,
    card_name    TEXT NOT NULL,
    set_name     TEXT NOT NULL,
    number       TEXT NOT NULL,
    rarity       TEXT,
    market_price REAL,
    hamming_dist INTEGER NOT NULL,
    is_corrected INTEGER NOT NULL DEFAULT 0,
    scan_token   TEXT,
    price_source TEXT,
    holo_type    TEXT,
    holo_variants TEXT
);
"""

_CREATE_CANDIDATES = """
CREATE TABLE IF NOT EXISTS scan_candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id      INTEGER NOT NULL REFERENCES scan_log(id),
    card_id      TEXT NOT NULL,
    card_name    TEXT NOT NULL,
    set_name     TEXT NOT NULL,
    number       TEXT NOT NULL,
    rarity       TEXT,
    hamming_dist INTEGER NOT NULL
);
"""


class ScanLogger:
    def __init__(self, db_path: str = config.SCAN_LOG_PATH):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_SCAN_LOG)
        self._conn.execute(_CREATE_CANDIDATES)
        self._conn.commit()
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
                pass  # column already exists

    def log_scan(self, record: ScanRecord) -> int:
        """Insert scan and its candidates. Returns the new scan_log row id."""
        cur = self._conn.execute(
            """INSERT INTO scan_log
               (session_id, scanned_at, card_id, card_name, set_name, number,
                rarity, market_price, hamming_dist, is_corrected, scan_token)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record.session_id, record.scanned_at, record.card_id,
                record.card_name, record.set_name, record.number,
                record.rarity, record.market_price, record.hamming_dist,
                int(record.is_corrected), record.scan_token,
            ),
        )
        scan_id = cur.lastrowid

        for c in record.candidates:
            self._conn.execute(
                """INSERT INTO scan_candidates
                   (scan_id, card_id, card_name, set_name, number, rarity, hamming_dist)
                   VALUES (?,?,?,?,?,?,?)""",
                (scan_id, c["card_id"], c["card_name"], c["set_name"],
                 c["number"], c.get("rarity"), c["hamming_dist"]),
            )

        self._conn.commit()
        return scan_id

    def get_session_scans(self, session_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM scan_log WHERE session_id = ? ORDER BY scanned_at",
            (session_id,),
        ).fetchall()

    def get_candidates(self, scan_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM scan_candidates WHERE scan_id = ? ORDER BY hamming_dist",
            (scan_id,),
        ).fetchall()

    def candidate_count(self, scan_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM scan_candidates WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        return row[0]

    def update_price(self, scan_id: int, market_price: float | None,
                     price_source: str | None = None) -> None:
        """Backfill price once the background fetch completes."""
        self._conn.execute(
            "UPDATE scan_log SET market_price=?, price_source=? WHERE id=?",
            (market_price, price_source, scan_id),
        )
        self._conn.commit()

    def update_holo(self, scan_id: int,
                    holo_type: str | None,
                    holo_variants: list | None) -> None:
        self._conn.execute(
            "UPDATE scan_log SET holo_type=?, holo_variants=? WHERE id=?",
            (holo_type,
             json.dumps(holo_variants) if holo_variants is not None else None,
             scan_id),
        )
        self._conn.commit()

    def resolve(self, scan_id: int, card_id: str, card_name: str, set_name: str,
                number: str, rarity: str | None, market_price: float | None) -> None:
        """Update a scan entry with the user-selected correct card."""
        self._conn.execute(
            """UPDATE scan_log SET
               card_id=?, card_name=?, set_name=?, number=?, rarity=?,
               market_price=?, is_corrected=1
               WHERE id=?""",
            (card_id, card_name, set_name, number, rarity, market_price, scan_id),
        )
        self._conn.commit()

    def export_csv(self, filepath: str) -> None:
        rows = self._conn.execute(
            "SELECT * FROM scan_log ORDER BY scanned_at"
        ).fetchall()
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "scanned_at", "session_id", "card_id", "card_name",
                "set_name", "number", "rarity", "market_price",
                "hamming_dist", "is_corrected", "holo_type", "holo_variants",
            ])
            for row in rows:
                writer.writerow([
                    row["scanned_at"], row["session_id"], row["card_id"],
                    row["card_name"], row["set_name"], row["number"],
                    row["rarity"], row["market_price"],
                    row["hamming_dist"], row["is_corrected"],
                    row["holo_type"], row["holo_variants"],
                ])

    def export_json(self, session_id: str, filepath: str) -> None:
        rows = self.get_session_scans(session_id)
        data = [dict(row) for row in rows]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "scans": data}, f, indent=2)

    def delete_scan(self, scan_id: int) -> None:
        self._conn.execute("DELETE FROM scan_candidates WHERE scan_id = ?", (scan_id,))
        self._conn.execute("DELETE FROM scan_log WHERE id = ?", (scan_id,))
        self._conn.commit()

    def get_scan(self, scan_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM scan_log WHERE id = ?", (scan_id,)
        ).fetchone()

    def close(self) -> None:
        self._conn.close()
