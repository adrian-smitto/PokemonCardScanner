import cv2
import numpy as np
import config


_diag_counter = 0   # throttle diagnostic logging


def detect_card(frame: np.ndarray, diag_callback=None) -> np.ndarray | None:
    """
    Detect a card-like quadrilateral in the frame.
    Returns a (4, 1, 2) contour array if found, else None.

    If diag_callback is provided it is called with a diagnostic string every
    ~90 frames (~3 s at 30 fps) when no card is found.
    """
    global _diag_counter
    _diag_counter += 1
    emit_diag = diag_callback is not None and _diag_counter % 90 == 0

    frame_area = frame.shape[0] * frame.shape[1]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    kernel = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0

    for contour in contours:
        area = cv2.contourArea(contour)
        if not _area_ok(area, frame_area):
            continue

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

        if len(approx) != 4:
            continue

        if not _aspect_ratio_ok(approx):
            continue

        if area > best_area:
            best_area = area
            best = approx

    if best is None and emit_diag:
        diag_callback(
            f"[detector] no card found in {frame.shape[1]}×{frame.shape[0]} frame "
            f"({len(contours)} contours)",
            "dim",
        )

    return best


def _area_ok(area: float, frame_area: int) -> bool:
    fraction = area / frame_area
    return config.CARD_AREA_MIN_FRACTION <= fraction <= config.CARD_AREA_MAX_FRACTION


def _aspect_ratio_ok(contour: np.ndarray) -> bool:
    x, y, w, h = cv2.boundingRect(contour)
    if h == 0:
        return False
    ratio = w / h
    portrait_ok  = abs(ratio - config.CARD_ASPECT_RATIO) <= config.ASPECT_RATIO_TOLERANCE
    landscape_ok = abs(ratio - (1 / config.CARD_ASPECT_RATIO)) <= config.ASPECT_RATIO_TOLERANCE
    return portrait_ok or landscape_ok
