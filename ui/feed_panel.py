import tkinter as tk
import cv2
import numpy as np
from PIL import Image, ImageTk
import config
from core.roi import ROI


class FeedPanel(tk.Frame):
    def __init__(self, parent, on_zoom_change=None, on_roi_change=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_zoom_change = on_zoom_change
        self._on_roi_change = on_roi_change
        self._zoom = config.DIGITAL_ZOOM_DEFAULT

        # ROI drag state (canvas/display coordinates)
        self._drag_start: tuple[int, int] | None = None
        self._drag_rect_id = None

        # Persistent ROI overlay drawn on the canvas (display coords)
        self._roi_rect_id = None
        self._roi_display: tuple[int, int, int, int] | None = None  # dx1,dy1,dx2,dy2

        self._toast_after_id = None

        self._build()

    def _build(self) -> None:
        self._canvas = tk.Canvas(
            self,
            width=config.UI_FEED_WIDTH,
            height=config.UI_FEED_HEIGHT,
            bg="black",
            cursor="crosshair",
        )
        self._canvas.pack()
        self._photo = None

        self._overlay_label = tk.Label(self, text="Waiting for camera...", fg="white", bg="black")
        self._overlay_label.place(relx=0.5, rely=0.5, anchor="center")

        # Toast confirmation label (shown briefly after ROI is set/cleared)
        self._toast_label = tk.Label(
            self._canvas, font=("Helvetica", 10, "bold"),
            fg="white", bg="#333333", padx=8, pady=4,
        )

        # Bind drag events
        self._canvas.bind("<ButtonPress-1>", self._drag_start_cb)
        self._canvas.bind("<B1-Motion>", self._drag_move_cb)
        self._canvas.bind("<ButtonRelease-1>", self._drag_end_cb)

        # Controls bar
        ctrl_bar = tk.Frame(self, bg="#1e1e1e")
        ctrl_bar.pack(fill="x")

        tk.Button(ctrl_bar, text="−", width=3, command=self._zoom_out,
                  font=("Helvetica", 11)).pack(side="left", padx=(4, 2), pady=3)
        self._zoom_var = tk.StringVar(value=f"{self._zoom:.2f}×")
        tk.Label(ctrl_bar, textvariable=self._zoom_var, width=6,
                 font=("Helvetica", 10), fg="white", bg="#1e1e1e").pack(side="left")
        tk.Button(ctrl_bar, text="+", width=3, command=self._zoom_in,
                  font=("Helvetica", 11)).pack(side="left", padx=(2, 4), pady=3)
        tk.Label(ctrl_bar, text="Zoom", font=("Helvetica", 9),
                 fg="#888888", bg="#1e1e1e").pack(side="left", padx=(0, 12))

        tk.Button(ctrl_bar, text="Clear ROI", command=self._clear_roi,
                  font=("Helvetica", 9)).pack(side="left", padx=(0, 4), pady=3)

        tk.Label(ctrl_bar, text="Drag to set scan area", font=("Helvetica", 9),
                 fg="#888888", bg="#1e1e1e").pack(side="left")

        # Hamming threshold slider (right-aligned)
        tk.Label(ctrl_bar, text="Max dist:", font=("Helvetica", 9),
                 fg="#888888", bg="#1e1e1e").pack(side="right", padx=(4, 0))
        self._threshold_var = tk.IntVar(value=config.MATCH_HAMMING_THRESHOLD)
        self._threshold_label = tk.Label(
            ctrl_bar, textvariable=self._threshold_var, width=3,
            font=("Helvetica", 9, "bold"), fg="white", bg="#1e1e1e",
        )
        self._threshold_label.pack(side="right")
        tk.Scale(
            ctrl_bar, from_=10, to=150, orient="horizontal",
            variable=self._threshold_var, length=120, showvalue=False,
            bg="#1e1e1e", fg="white", troughcolor="#333333", highlightthickness=0,
            command=self._on_threshold_change,
        ).pack(side="right", padx=(0, 4))

    # ── Frame display ────────────────────────────────────────────────────────

    def update_frame(self, frame: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb).resize(
            (config.UI_FEED_WIDTH, config.UI_FEED_HEIGHT), Image.BILINEAR
        )
        self._photo = ImageTk.PhotoImage(img)
        self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._overlay_label.place_forget()

        # Re-draw persistent ROI rect on top of the new frame image
        self._redraw_roi_rect()

    def show_error(self, message: str) -> None:
        self._overlay_label.config(text=message)
        self._overlay_label.place(relx=0.5, rely=0.5, anchor="center")

    # ── ROI overlay ──────────────────────────────────────────────────────────

    def set_roi_display(self, roi: ROI | None) -> None:
        """Called by app_window when a saved ROI is restored on startup."""
        if roi is None:
            self._roi_display = None
        else:
            scale_x = config.UI_FEED_WIDTH / config.CAMERA_WIDTH
            scale_y = config.UI_FEED_HEIGHT / config.CAMERA_HEIGHT
            self._roi_display = (
                int(roi.x1 * scale_x), int(roi.y1 * scale_y),
                int(roi.x2 * scale_x), int(roi.y2 * scale_y),
            )
        self._redraw_roi_rect()

    def _redraw_roi_rect(self) -> None:
        if self._roi_rect_id:
            self._canvas.delete(self._roi_rect_id)
            self._roi_rect_id = None
        if self._roi_display:
            dx1, dy1, dx2, dy2 = self._roi_display
            self._roi_rect_id = self._canvas.create_rectangle(
                dx1, dy1, dx2, dy2,
                outline="#00bcd4", width=2,
            )

    def _show_toast(self, message: str) -> None:
        if self._toast_after_id:
            self._canvas.after_cancel(self._toast_after_id)
        self._toast_label.config(text=message)
        self._toast_label.place(relx=0.5, rely=0.05, anchor="n")
        self._toast_after_id = self._canvas.after(2000, self._hide_toast)

    def _hide_toast(self) -> None:
        self._toast_label.place_forget()
        self._toast_after_id = None

    # ── Zoom ────────────────────────────────────────────────────────────────

    def _zoom_in(self) -> None:
        self._set_zoom(self._zoom + config.DIGITAL_ZOOM_STEP)

    def _zoom_out(self) -> None:
        self._set_zoom(self._zoom - config.DIGITAL_ZOOM_STEP)

    def _set_zoom(self, value: float) -> None:
        self._zoom = round(
            max(config.DIGITAL_ZOOM_MIN, min(config.DIGITAL_ZOOM_MAX, value)), 2
        )
        self._zoom_var.set(f"{self._zoom:.2f}×")
        if self._on_zoom_change:
            self._on_zoom_change(self._zoom)

    # ── ROI drag ────────────────────────────────────────────────────────────

    def _drag_start_cb(self, event) -> None:
        self._drag_start = (event.x, event.y)
        if self._drag_rect_id:
            self._canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None

    def _drag_move_cb(self, event) -> None:
        if not self._drag_start:
            return
        if self._drag_rect_id:
            self._canvas.delete(self._drag_rect_id)
        x0, y0 = self._drag_start
        self._drag_rect_id = self._canvas.create_rectangle(
            x0, y0, event.x, event.y,
            outline="#00bcd4", width=2, dash=(4, 4),
        )

    def _drag_end_cb(self, event) -> None:
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y
        self._drag_start = None

        if self._drag_rect_id:
            self._canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None

        rx1, rx2 = (x0, x1) if x0 < x1 else (x1, x0)
        ry1, ry2 = (y0, y1) if y0 < y1 else (y1, y0)

        if rx2 - rx1 < 10 or ry2 - ry1 < 10:
            return

        # Store display coords for persistent overlay
        self._roi_display = (rx1, ry1, rx2, ry2)
        self._redraw_roi_rect()
        self._show_toast("Scan area set")

        roi = self._display_to_frame_roi(rx1, ry1, rx2, ry2)
        if self._on_roi_change:
            self._on_roi_change(roi)

    def _clear_roi(self) -> None:
        self._roi_display = None
        if self._roi_rect_id:
            self._canvas.delete(self._roi_rect_id)
            self._roi_rect_id = None
        self._show_toast("Scan area cleared")
        if self._on_roi_change:
            self._on_roi_change(None)

    def _on_threshold_change(self, _value) -> None:
        config.MATCH_HAMMING_THRESHOLD = self._threshold_var.get()

    def _display_to_frame_roi(self, dx1: int, dy1: int, dx2: int, dy2: int) -> ROI:
        scale_x = config.CAMERA_WIDTH / config.UI_FEED_WIDTH
        scale_y = config.CAMERA_HEIGHT / config.UI_FEED_HEIGHT
        return ROI(
            x1=int(dx1 * scale_x),
            y1=int(dy1 * scale_y),
            x2=int(dx2 * scale_x),
            y2=int(dy2 * scale_y),
        )
