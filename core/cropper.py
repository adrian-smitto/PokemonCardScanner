import cv2
import numpy as np
from PIL import Image


def crop_and_correct(
    frame: np.ndarray,
    contour: np.ndarray,
    output_size: tuple[int, int] = (300, 420),
) -> Image.Image:
    """
    Perspective-correct the detected card quad and return a PIL Image.
    output_size is (width, height).
    """
    pts = contour.reshape(4, 2).astype(np.float32)
    src = _order_points(pts)

    w, h = output_size
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)

    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(frame, M, (w, h))

    rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _order_points(pts: np.ndarray) -> np.ndarray:
    """
    Return points ordered: top-left, top-right, bottom-right, bottom-left.
    """
    ordered = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    ordered[0] = pts[np.argmin(s)]   # top-left: smallest sum
    ordered[2] = pts[np.argmax(s)]   # bottom-right: largest sum

    diff = np.diff(pts, axis=1)
    ordered[1] = pts[np.argmin(diff)]  # top-right: smallest diff
    ordered[3] = pts[np.argmax(diff)]  # bottom-left: largest diff

    return ordered
