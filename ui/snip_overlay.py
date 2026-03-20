import ctypes
import tkinter as tk


def _virtual_desktop() -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the virtual desktop spanning all monitors."""
    u32 = ctypes.windll.user32
    u32.SetProcessDPIAware()
    x = u32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    y = u32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = u32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = u32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    return x, y, w, h


class SnipOverlay:
    """
    Full-virtual-desktop semi-transparent overlay for selecting a screen region.

    on_capture(bbox)  — called with (x1, y1, x2, y2) in virtual screen coordinates
    on_cancel()       — called when Escape is pressed (optional)
    """

    def __init__(self, parent: tk.Tk, on_capture, on_cancel=None):
        self._on_capture = on_capture
        self._on_cancel = on_cancel
        self._vx, self._vy, vw, vh = _virtual_desktop()
        self._start: tuple[int, int] | None = None
        self._rect_id = None

        self._win = tk.Toplevel(parent)
        self._win.geometry(f"{vw}x{vh}+{self._vx}+{self._vy}")
        self._win.attributes('-alpha', 0.25)
        self._win.attributes('-topmost', True)
        self._win.overrideredirect(True)
        self._win.configure(bg='grey')
        self._win.grab_set()

        self._canvas = tk.Canvas(
            self._win, cursor='crosshair', bg='grey', highlightthickness=0,
        )
        self._canvas.pack(fill='both', expand=True)

        self._canvas.bind('<ButtonPress-1>',   self._on_press)
        self._canvas.bind('<B1-Motion>',       self._on_drag)
        self._canvas.bind('<ButtonRelease-1>', self._on_release)
        self._win.bind('<Escape>',             self._on_escape)

    def _on_press(self, event) -> None:
        self._start = (event.x, event.y)
        if self._rect_id:
            self._canvas.delete(self._rect_id)
            self._rect_id = None

    def _on_drag(self, event) -> None:
        if not self._start:
            return
        if self._rect_id:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            *self._start, event.x, event.y,
            outline='red', width=2, fill='',
        )

    def _on_release(self, event) -> None:
        if not self._start:
            return
        # Convert widget-local coords to virtual screen coords
        x1 = self._vx + min(self._start[0], event.x)
        y1 = self._vy + min(self._start[1], event.y)
        x2 = self._vx + max(self._start[0], event.x)
        y2 = self._vy + max(self._start[1], event.y)
        self._win.destroy()
        if x2 - x1 > 5 and y2 - y1 > 5:
            self._on_capture((x1, y1, x2, y2))

    def _on_escape(self, _event) -> None:
        self._win.destroy()
        if self._on_cancel:
            self._on_cancel()
