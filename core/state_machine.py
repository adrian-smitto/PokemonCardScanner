import queue
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, auto

import cv2
import numpy as np
from PIL import Image

import config
from core.detector import detect_card
from core.cropper import crop_and_correct
from core.hasher import compute_phash, hamming
from core.matcher import CardMatcher, MatchResult
from core.price_client import PriceClient, PriceResult
from core.roi import ROI
from core import audio


class ScanState(Enum):
    IDLE = auto()
    CARD_DETECTED = auto()
    STABILIZING = auto()
    MATCHING = auto()
    COOLDOWN = auto()


@dataclass
class ScanResult:
    scan_token: str          # unique id — used to correlate the later PriceUpdate
    session_id: str
    card_id: str
    card_name: str
    set_name: str
    number: str
    rarity: str | None
    market_price: float | None
    low_price: float | None
    high_price: float | None
    hamming_dist: int
    price_error: str | None
    candidates: list[dict] = field(default_factory=list)


@dataclass
class PriceUpdate:
    scan_token: str
    market_price: float | None
    low_price: float | None
    high_price: float | None
    price_error: str | None


class ScanStateMachine:
    def __init__(self, session_id: str, on_status=None):
        self._session_id = session_id
        self._matcher = CardMatcher()
        self._price_client = PriceClient()
        self._executor = ThreadPoolExecutor(max_workers=4)
        self.result_queue: queue.Queue[ScanResult] = queue.Queue()
        self.price_update_queue: queue.Queue[PriceUpdate] = queue.Queue()
        self.roi: ROI | None = None
        self._on_status = on_status  # callback(message, level)

        self._state = ScanState.IDLE
        self._stable_hashes: list = []
        self._last_contour: np.ndarray | None = None
        self._cooldown_start: float = 0.0
        self._cooldown_hash = None
        self._match_future = None
        self._pending_prices: list = []   # [(future, scan_token, card_name)]
        self._last_card_id: str | None = None   # suppress duplicate scans
        self._lost_frames: int = 0        # consecutive frames without a contour

    @property
    def state(self) -> ScanState:
        return self._state

    def process(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        """
        Drive the state machine for one frame.
        Returns (annotated_frame, contour_or_None).
        If an ROI is set, detection runs on the cropped region and the contour
        is translated back to full-frame coordinates for overlay drawing.
        """
        annotated = frame.copy()
        roi = self.roi

        # Drain completed background price fetches (runs every frame regardless of state)
        self._drain_price_futures()

        # --- Card detection ---
        # ROI mode: the card sits in a fixed slot — Canny is useless because the
        # outer card border is hidden by the box walls.  Use brightness instead.
        # Full-frame mode: classic Canny contour detection.
        if roi and roi.is_valid():
            roi_region = frame[roi.y1:roi.y2, roi.x1:roi.x2]
            brightness = float(np.mean(cv2.cvtColor(roi_region, cv2.COLOR_BGR2GRAY)))
            card_present = brightness > config.ROI_BRIGHTNESS_THRESHOLD
            contour = self._roi_contour(roi) if card_present else None
        else:
            contour = detect_card(
                frame,
                diag_callback=self._on_status if self._state == ScanState.IDLE else None,
            )

        if self._state == ScanState.IDLE:
            self._lost_frames = 0
            if contour is not None:
                self._status("Card detected — stabilizing...", "detect")
                self._transition(ScanState.CARD_DETECTED)
                self._last_contour = contour

        elif self._state == ScanState.CARD_DETECTED:
            if contour is None:
                self._lost_frames += 1
                if self._lost_frames >= config.LOST_FRAMES_THRESHOLD:
                    self._status("Card removed before stabilizing", "dim")
                    self._lost_frames = 0
                    self._transition(ScanState.IDLE)
            else:
                self._lost_frames = 0
                self._last_contour = contour
                self._stable_hashes = []
                self._transition(ScanState.STABILIZING)

        elif self._state == ScanState.STABILIZING:
            if contour is None:
                self._lost_frames += 1
                if self._lost_frames >= config.LOST_FRAMES_THRESHOLD:
                    self._status("Card removed during stabilization", "dim")
                    self._stable_hashes = []
                    self._lost_frames = 0
                    self._transition(ScanState.IDLE)
            else:
                self._lost_frames = 0
                self._last_contour = contour
                try:
                    cropped = self._crop_card(frame, contour)
                    h = compute_phash(cropped)
                    try:
                        cropped.save("debug_crop.jpg")
                    except Exception:
                        pass
                    if self._stable_hashes:
                        drift = hamming(h, self._stable_hashes[-1])
                        if drift > config.STABILIZE_HASH_THRESHOLD:
                            self._stable_hashes = [h]
                        else:
                            self._stable_hashes.append(h)
                    else:
                        self._stable_hashes.append(h)
                except Exception:
                    pass

                if len(self._stable_hashes) % 2 == 0:
                    self._status(
                        f"Stabilizing... {len(self._stable_hashes)}/{config.STABILIZE_FRAMES} frames", "detect"
                    )

                if len(self._stable_hashes) >= config.STABILIZE_FRAMES:
                    self._status("Stable — running hash match...", "info")
                    canonical = self._canonical_hash(self._stable_hashes)
                    self._transition(ScanState.MATCHING)
                    self._match_future = self._executor.submit(
                        self._matcher.find_matches, canonical
                    )

        elif self._state == ScanState.MATCHING:
            if self._match_future and self._match_future.done():
                result: MatchResult | None = self._match_future.result()
                self._match_future = None
                if result is None:
                    closest = self._matcher.last_closest_dist
                    self._status(f"No match found (closest dist={closest})", "error")
                    audio.play_failure()
                    self._transition(ScanState.IDLE)
                else:
                    if result.primary.card_id == self._last_card_id:
                        self._status("Same card — skipping", "dim")
                        self._cooldown_start = time.monotonic()
                        self._cooldown_hash = self._stable_hashes[-1] if self._stable_hashes else None
                        self._stable_hashes = []
                        self._transition(ScanState.COOLDOWN)
                        return annotated, contour

                    alts = len(result.candidates)
                    self._status(
                        f"Matched: {result.primary.name} [{result.primary.set_name} #{result.primary.number}]"
                        f" — dist={result.primary.hamming_dist}"
                        + (f" ({alts} alternative{'s' if alts > 1 else ''})" if alts else ""),
                        "match"
                    )

                    scan_token = str(uuid.uuid4())
                    candidates = [
                        {
                            "card_id": c.card_id,
                            "card_name": c.name,
                            "set_name": c.set_name,
                            "number": c.number,
                            "rarity": c.rarity,
                            "hamming_dist": c.hamming_dist,
                        }
                        for c in result.candidates
                    ]

                    scan_result = ScanResult(
                        scan_token=scan_token,
                        session_id=self._session_id,
                        card_id=result.primary.card_id,
                        card_name=result.primary.name,
                        set_name=result.primary.set_name,
                        number=result.primary.number,
                        rarity=result.primary.rarity,
                        market_price=None,
                        low_price=None,
                        high_price=None,
                        hamming_dist=result.primary.hamming_dist,
                        price_error=None,
                        candidates=candidates,
                    )
                    self._last_card_id = result.primary.card_id
                    self.result_queue.put(scan_result)
                    audio.play_card_scanned()

                    # Kick off price fetch in background — result arrives via price_update_queue
                    price_future = self._executor.submit(
                        self._price_client.fetch_price, result.primary.card_id
                    )
                    self._pending_prices.append((price_future, scan_token, result.primary.name))

                    self._cooldown_start = time.monotonic()
                    self._cooldown_hash = self._stable_hashes[-1] if self._stable_hashes else None
                    self._stable_hashes = []
                    self._transition(ScanState.COOLDOWN)

        elif self._state == ScanState.COOLDOWN:
            elapsed = time.monotonic() - self._cooldown_start
            card_removed = contour is None

            if self._cooldown_hash and contour is not None:
                try:
                    cropped = self._crop_card(frame, contour)
                    current_hash = compute_phash(cropped)
                    drift = hamming(current_hash, self._cooldown_hash)
                    if drift > config.STABILIZE_HASH_THRESHOLD * 2:
                        card_removed = True
                except Exception:
                    pass

            if card_removed or elapsed >= config.DUPLICATE_COOLDOWN_SECONDS:
                self._status("Ready", "dim")
                self._cooldown_hash = None
                self._transition(ScanState.IDLE)

        # Draw ROI border (colour reflects detection state)
        if roi and roi.is_valid():
            roi_color = self._roi_color()
            cv2.rectangle(annotated, (roi.x1, roi.y1), (roi.x2, roi.y2), roi_color, 2)
        else:
            if contour is not None and self._state in (ScanState.CARD_DETECTED, ScanState.STABILIZING):
                cv2.polylines(annotated, [contour], True, (0, 255, 255), 2)
            elif contour is not None and self._state in (ScanState.MATCHING, ScanState.COOLDOWN):
                cv2.polylines(annotated, [contour], True, (0, 255, 0), 2)

        return annotated, contour

    def _drain_price_futures(self) -> None:
        """Check all pending background price fetches and emit updates for completed ones."""
        still_pending = []
        for future, scan_token, card_name in self._pending_prices:
            if not future.done():
                still_pending.append((future, scan_token, card_name))
                continue
            try:
                price: PriceResult = future.result()
            except Exception as e:
                price = PriceResult(card_id="", market_price=None,
                                    low_price=None, high_price=None, error=str(e))
            if price.available:
                self._status(f"Price: ${price.market_price:.2f} ({card_name})", "success")
            else:
                self._status(f"Price unavailable for {card_name}" + (f" — {price.error}" if price.error else ""), "error")
            self.price_update_queue.put(PriceUpdate(
                scan_token=scan_token,
                market_price=price.market_price,
                low_price=price.low_price,
                high_price=price.high_price,
                price_error=price.error,
            ))
        self._pending_prices = still_pending

    def _roi_color(self) -> tuple[int, int, int]:
        """BGR colour for the ROI border based on current state."""
        if self._state in (ScanState.MATCHING, ScanState.COOLDOWN):
            return (0, 255, 0)    # green
        if self._state in (ScanState.CARD_DETECTED, ScanState.STABILIZING):
            return (0, 255, 255)  # yellow
        return (180, 180, 180)    # dim white — idle

    def _crop_card(self, frame: np.ndarray, contour: np.ndarray) -> Image.Image:
        """Crop and normalise the detected card for hashing.

        ROI mode: card is in a fixed slot, crop directly (no warp needed).
        Full-frame mode: perspective-correct via the detected quad.
        """
        roi = self.roi
        if roi and roi.is_valid():
            crop = frame[roi.y1:roi.y2, roi.x1:roi.x2]
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb).resize((300, 420), Image.Resampling.BILINEAR)
        return crop_and_correct(frame, contour)

    def _roi_contour(self, roi: ROI) -> np.ndarray:
        """Synthetic 4-point contour representing the ROI rectangle."""
        return np.array([
            [[roi.x1, roi.y1]],
            [[roi.x2, roi.y1]],
            [[roi.x2, roi.y2]],
            [[roi.x1, roi.y2]],
        ], dtype=np.int32)

    def _canonical_hash(self, hashes: list):
        best = hashes[0]
        best_total = sum(hamming(hashes[0], h) for h in hashes)
        for h in hashes[1:]:
            total = sum(hamming(h, other) for other in hashes)
            if total < best_total:
                best_total = total
                best = h
        return best

    def _status(self, message: str, level: str = "info") -> None:
        if self._on_status:
            self._on_status(message, level)

    def _transition(self, new_state: ScanState) -> None:
        self._state = new_state

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
        self._matcher.close()
