import sqlite3
from dataclasses import dataclass, field

import imagehash

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
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, set_name, number, rarity, phash FROM cards"
        ).fetchall()
        conn.close()

        # Pre-parse all hashes once at startup; _cards is immutable after this
        self._cards: list[tuple[imagehash.ImageHash, CardCandidate]] = []
        for row in rows:
            try:
                h = str_to_hash(row["phash"])
            except Exception:
                continue
            self._cards.append((h, CardCandidate(
                card_id=row["id"],
                name=row["name"],
                set_name=row["set_name"],
                number=row["number"],
                rarity=row["rarity"],
                hamming_dist=0,
            )))

        self.last_closest_dist: int = 999

    def find_matches(self, query_hash: imagehash.ImageHash,
                     fallback_hash: imagehash.ImageHash | None = None) -> MatchResult | None:
        """Scan the DB with query_hash; if no match, try fallback_hash (e.g. 180° rotation)."""
        result = self._scan(query_hash)
        if result is None and fallback_hash is not None:
            result = self._scan(fallback_hash)
        return result

    def _scan(self, query_hash: imagehash.ImageHash) -> MatchResult | None:
        hits: list[CardCandidate] = []
        closest_dist = 999

        for db_hash, card in self._cards:
            dist = int(query_hash - db_hash)

            if dist < closest_dist:
                closest_dist = dist

            if dist <= config.MATCH_HAMMING_THRESHOLD:
                hits.append(CardCandidate(
                    card_id=card.card_id,
                    name=card.name,
                    set_name=card.set_name,
                    number=card.number,
                    rarity=card.rarity,
                    hamming_dist=dist,
                ))

        self.last_closest_dist = closest_dist

        if not hits:
            return None

        hits.sort(key=lambda c: c.hamming_dist)
        return MatchResult(primary=hits[0], candidates=hits[1:], closest_dist=closest_dist)

    def find_top_n(self, query_hash, n: int = 100) -> list[CardCandidate]:
        """Return the top-N closest cards by hamming distance, ignoring threshold."""
        results = []
        for db_hash, card in self._cards:
            dist = int(query_hash - db_hash)
            results.append(CardCandidate(
                card_id=card.card_id,
                name=card.name,
                set_name=card.set_name,
                number=card.number,
                rarity=card.rarity,
                hamming_dist=dist,
            ))
        results.sort(key=lambda c: c.hamming_dist)
        return results[:n]

    def close(self) -> None:
        pass  # nothing to close — DB connection released at startup
