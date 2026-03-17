import sqlite3
from dataclasses import dataclass, field

import imagehash
import numpy as np

import config
from core.hasher import str_to_hash


@dataclass
class CardCandidate:
    card_id: str
    name: str
    set_name: str
    number: str
    rarity: str | None
    hamming_dist: int


@dataclass
class MatchResult:
    primary: CardCandidate
    candidates: list[CardCandidate] = field(default_factory=list)
    closest_dist: int = 0   # best hamming distance found (even if above threshold)


class CardMatcher:
    def __init__(self, db_path: str = config.DB_PATH):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.last_closest_dist: int = 999

    def find_matches(self, query_hash: imagehash.ImageHash) -> MatchResult | None:
        """
        Full-table hamming scan. Returns a MatchResult with the best match as
        primary and all other matches within threshold as candidates.
        Returns None if no match is found within the threshold.
        """
        rows = self._conn.execute(
            "SELECT id, name, set_name, number, rarity, phash FROM cards"
        ).fetchall()

        hits: list[CardCandidate] = []
        closest_dist = 999
        closest_candidate: CardCandidate | None = None

        for row in rows:
            try:
                db_hash = str_to_hash(row["phash"])
                dist = int(query_hash - db_hash)
            except Exception:
                continue

            if dist < closest_dist:
                closest_dist = dist
                closest_candidate = CardCandidate(
                    card_id=row["id"],
                    name=row["name"],
                    set_name=row["set_name"],
                    number=row["number"],
                    rarity=row["rarity"],
                    hamming_dist=dist,
                )

            if dist <= config.MATCH_HAMMING_THRESHOLD:
                hits.append(CardCandidate(
                    card_id=row["id"],
                    name=row["name"],
                    set_name=row["set_name"],
                    number=row["number"],
                    rarity=row["rarity"],
                    hamming_dist=dist,
                ))

        self.last_closest_dist = closest_dist

        if not hits:
            return None

        hits.sort(key=lambda c: c.hamming_dist)
        return MatchResult(primary=hits[0], candidates=hits[1:], closest_dist=closest_dist)

    def close(self) -> None:
        self._conn.close()
