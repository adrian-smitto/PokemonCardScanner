import os
import tkinter as tk
from datetime import datetime

LOG_FILE = "debug.log"


class DebugLog(tk.Frame):
    """Scrollable status log showing app activity in real time."""

    MAX_LINES = 200

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#0d0d0d", **kwargs)
        self._file = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        self._file.write(f"\n--- Session started {datetime.now().isoformat()} ---\n")
        self._build()

    def _build(self) -> None:
        header = tk.Frame(self, bg="#0d0d0d")
        header.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(header, text="STATUS LOG", font=("Courier", 8, "bold"),
                 fg="#555555", bg="#0d0d0d").pack(side="left")
        tk.Button(header, text="Clear", font=("Courier", 8),
                  fg="#555555", bg="#0d0d0d", bd=0, activebackground="#1a1a1a",
                  command=self.clear).pack(side="right")

        self._text = tk.Text(
            self, height=5, bg="#0d0d0d", fg="#00ff88",
            font=("Courier", 9), state="disabled",
            relief="flat", bd=0, wrap="word",
        )
        self._text.pack(fill="both", expand=True, padx=6, pady=(2, 4))

        vsb = tk.Scrollbar(self, command=self._text.yview, bg="#0d0d0d")
        vsb.pack(side="right", fill="y")
        self._text.configure(yscrollcommand=vsb.set)

        # Colour tags
        self._text.tag_config("info",    foreground="#00ff88")
        self._text.tag_config("detect",  foreground="#00bcd4")
        self._text.tag_config("match",   foreground="#ffeb3b")
        self._text.tag_config("success", foreground="#69f0ae")
        self._text.tag_config("error",   foreground="#ef5350")
        self._text.tag_config("dim",     foreground="#444444")

    def log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self._text.configure(state="normal")
        line_count = int(self._text.index("end-1c").split(".")[0])
        if line_count > self.MAX_LINES:
            self._text.delete("1.0", f"{line_count - self.MAX_LINES}.0")
        self._text.insert("end", line, level)
        self._text.see("end")
        self._text.configure(state="disabled")
        self._file.write(f"[{ts}] [{level.upper():7}] {message}\n")

    def clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")

    def close(self) -> None:
        self._file.write(f"--- Session ended {datetime.now().isoformat()} ---\n")
        self._file.close()
