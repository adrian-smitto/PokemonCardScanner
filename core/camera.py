import threading
import cv2
import numpy as np
import config


class CameraCapture:
    """
    Captures frames from a camera in a background daemon thread.
    Latest frame is accessible via the latest_frame property (thread-safe).
    Digital zoom is applied in the capture loop before storing the frame.
    """

    def __init__(self):
        self._frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._zoom_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._digital_zoom: float = config.DIGITAL_ZOOM_DEFAULT
        self.error: str | None = None

    def start(self) -> bool:
        """
        Start the capture thread. Returns True if the camera opened successfully,
        False otherwise (check self.error for the reason).
        """
        cap = cv2.VideoCapture(config.CAMERA_INDEX)
        if not cap.isOpened():
            self.error = (
                f"Camera {config.CAMERA_INDEX} could not be opened. "
                f"Check CAMERA_INDEX in config.py."
            )
            return False
        cap.release()

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    @property
    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def digital_zoom(self) -> float:
        with self._zoom_lock:
            return self._digital_zoom

    @digital_zoom.setter
    def digital_zoom(self, value: float) -> None:
        value = max(config.DIGITAL_ZOOM_MIN, min(config.DIGITAL_ZOOM_MAX, value))
        with self._zoom_lock:
            self._digital_zoom = round(value, 2)

    def _capture_loop(self) -> None:
        cap = cv2.VideoCapture(config.CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)

        if config.CAMERA_NATIVE_ZOOM > 0:
            cap.set(cv2.CAP_PROP_ZOOM, config.CAMERA_NATIVE_ZOOM)

        while not self._stop_event.is_set():
            ret, frame = cap.read()
            if ret:
                frame = self._apply_digital_zoom(frame)
                with self._lock:
                    self._frame = frame

        cap.release()

    def _apply_digital_zoom(self, frame: np.ndarray) -> np.ndarray:
        with self._zoom_lock:
            zoom = self._digital_zoom

        if zoom <= 1.0:
            return frame

        h, w = frame.shape[:2]
        new_h = int(h / zoom)
        new_w = int(w / zoom)
        y1 = (h - new_h) // 2
        x1 = (w - new_w) // 2
        cropped = frame[y1:y1 + new_h, x1:x1 + new_w]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
