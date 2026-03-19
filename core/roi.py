"""
Region of Interest (ROI) — defines the scannable area within the camera frame.
Coordinates are stored in raw frame pixels (before digital zoom scaling).
Persisted to settings.json.
"""

from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass

SETTINGS_PATH = "settings.json"


@dataclass
class ROI:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    def is_valid(self) -> bool:
        return self.width > 10 and self.height > 10

    def to_dict(self) -> dict:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}

    @staticmethod
    def from_dict(d: dict) -> ROI:
        return ROI(x1=d["x1"], y1=d["y1"], x2=d["x2"], y2=d["y2"])


def load_roi() -> ROI | None:
    if not os.path.exists(SETTINGS_PATH):
        return None
    try:
        with open(SETTINGS_PATH, "r") as f:
            data = json.load(f)
        roi_data = data.get("roi")
        if roi_data:
            roi = ROI.from_dict(roi_data)
            return roi if roi.is_valid() else None
    except Exception:
        pass
    return None


def save_roi(roi: ROI | None) -> None:
    data = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    data["roi"] = roi.to_dict() if roi else None
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def load_setting(key: str, default):
    if not os.path.exists(SETTINGS_PATH):
        return default
    try:
        with open(SETTINGS_PATH, "r") as f:
            data = json.load(f)
        return data.get(key, default)
    except Exception:
        return default


def save_setting(key: str, value) -> None:
    data = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    data[key] = value
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def is_on_screen(geometry: str) -> bool:
    """Return False if the top-left corner is likely off all connected monitors."""
    m = re.match(r'\d+x\d+\+(-?\d+)\+(-?\d+)', geometry)
    if not m:
        return False
    x, y = int(m.group(1)), int(m.group(2))
    return x > -2000 and y > -2000
