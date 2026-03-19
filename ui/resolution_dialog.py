import os
import tkinter as tk
from tkinter import ttk
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageTk

import config
from core.price_client import PriceClient
from core.scan_log import ScanLogger
from core.roi import load_setting, save_setting, is_on_screen


class ResolutionDialog(tk.Toplevel):
    """
    Modal dialog for resolving an ambiguous scan.
    Shows the captured crop alongside candidate card images.
    """

    def __init__(self, parent, scan_id: int, logger: ScanLogger,
                 price_client: PriceClient, on_resolved, **kwargs):
        super().__init__(parent, **kwargs)
        self.title("Resolve Ambiguous Scan")
        self.resizable(True, True)
        self.grab_set()

        self._scan_id = scan_id
        self._logger = logger
        self._price_client = price_client
        self._on_resolved = on_resolved
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._selected_candidate = None
        self._thumb_cache: list = []  # keep PhotoImage refs alive

        candidates = logger.get_candidates(scan_id)
        self._candidates = [dict(row) for row in candidates]

        # Retrieve scan_token for capture image lookup
        scan_row = logger.get_scan(scan_id)
        self._scan_token = scan_row["scan_token"] if scan_row else None

        self.protocol("WM_DELETE_WINDOW", self._close)
        geo = load_setting("resolution_geometry", None)
        if geo and is_on_screen(geo):
            self.geometry(geo)

        self._build()
        self.transient(parent)
        self.wait_visibility()
        self.grab_set()

    def _build(self) -> None:
        # ── Top: capture image + instructions ──────────────────────────────
        top = tk.Frame(self, bg="#1e1e1e")
        top.pack(fill="x", padx=12, pady=(10, 6))

        self._capture_label = tk.Label(
            top, bg="#333333", text="No capture", fg="#888888",
            width=12, height=8,
        )
        self._capture_label.pack(side="left", padx=(0, 4))
        self._load_capture()

        # Arrow between capture and selected card preview
        tk.Label(top, text="→", font=("Helvetica", 14),
                 bg="#1e1e1e", fg="#888888").pack(side="left", padx=4)

        # Selected card preview (updates on row click)
        self._selected_label = tk.Label(
            top, bg="#333333", text="Select a card", fg="#888888",
            width=12, height=8,
        )
        self._selected_label.pack(side="left", padx=(4, 12))

        tk.Label(top, text="Select the correct card:",
                 font=("Helvetica", 11, "bold"),
                 bg="#1e1e1e", fg="white").pack(side="left", anchor="w")

        # ── Candidate list ──────────────────────────────────────────────────
        list_frame = tk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=12, pady=4)

        cols = ("thumb", "name", "set", "number", "rarity", "dist")
        height = min(max(len(self._candidates), 3), 10)
        self._tree = ttk.Treeview(list_frame, columns=cols,
                                  show="headings", height=height)
        self._tree.heading("thumb",  text="")
        self._tree.heading("name",   text="Card Name")
        self._tree.heading("set",    text="Set")
        self._tree.heading("number", text="#")
        self._tree.heading("rarity", text="Rarity")
        self._tree.heading("dist",   text="Confidence")

        self._tree.column("thumb",  width=50,  stretch=False)
        self._tree.column("name",   width=200, stretch=True)
        self._tree.column("set",    width=130, stretch=True)
        self._tree.column("number", width=40,  stretch=False)
        self._tree.column("rarity", width=90,  stretch=False)
        self._tree.column("dist",   width=80,  stretch=False)

        vsb = ttk.Scrollbar(list_frame, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for c in self._candidates:
            dist = c["hamming_dist"]
            confidence = f"{max(0, 100 - dist * 3)}%"
            thumb = self._load_thumb(c["card_id"])
            self._thumb_cache.append(thumb)
            iid = self._tree.insert("", "end", values=(
                "", c["card_name"], c["set_name"], c["number"],
                c.get("rarity") or "", confidence,
            ))
            if thumb:
                self._tree.item(iid, image=thumb)

        # ── Buttons / status ────────────────────────────────────────────────
        btn_frame = tk.Frame(self)
        btn_frame.pack(padx=12, pady=8, fill="x")

        self._confirm_btn = tk.Button(btn_frame, text="Confirm", width=12,
                                      command=self._confirm, state="disabled")
        self._confirm_btn.pack(side="right", padx=(5, 0))
        tk.Button(btn_frame, text="Cancel", width=12,
                  command=self._close).pack(side="right")

        self._status_var = tk.StringVar(value="Select a card to confirm.")
        tk.Label(self, textvariable=self._status_var, fg="#888888",
                 font=("Helvetica", 9, "italic")).pack(padx=12, pady=(0, 8))

        self._tree.bind("<<TreeviewSelect>>", self._on_select)

    def _load_capture(self) -> None:
        if not self._scan_token:
            return
        path = os.path.join(config.CAPTURES_DIR, f"{self._scan_token}.jpg")
        if not os.path.exists(path):
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((200, 280), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._capture_label.config(
                image=photo, text="", width=img.width, height=img.height)
            self._capture_label.image = photo
        except Exception:
            pass

    def _load_thumb(self, card_id: str) -> ImageTk.PhotoImage | None:
        safe_id = card_id.replace("/", "_")
        path = os.path.join(config.IMAGES_DIR, f"{safe_id}.jpg")
        if not os.path.exists(path):
            return None
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((36, 50), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _on_select(self, _event) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        idx = self._tree.index(sel[0])
        self._selected_candidate = self._candidates[idx]
        self._confirm_btn.config(state="normal")
        self._status_var.set(f"Selected: {self._selected_candidate['card_name']}")
        self._show_selected_preview(self._selected_candidate["card_id"])

    def _show_selected_preview(self, card_id: str) -> None:
        safe_id = card_id.replace("/", "_")
        path = os.path.join(config.IMAGES_DIR, f"{safe_id}.jpg")
        if not os.path.exists(path):
            self._selected_label.config(image="", text="No image", width=12, height=8)
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((200, 280), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._selected_label.config(
                image=photo, text="", width=img.width, height=img.height)
            self._selected_label.image = photo
        except Exception:
            self._selected_label.config(image="", text="No image", width=12, height=8)

    def _close(self) -> None:
        save_setting("resolution_geometry", self.geometry())
        self.destroy()

    def _confirm(self) -> None:
        if not self._selected_candidate:
            return
        self._confirm_btn.config(state="disabled", text="Fetching price...")
        self._status_var.set("Fetching price…")
        card_id = self._selected_candidate["card_id"]
        future = self._executor.submit(self._price_client.fetch_price, card_id)
        self.after(100, lambda: self._poll_price(future))

    def _poll_price(self, future) -> None:
        if not future.done():
            self.after(100, lambda: self._poll_price(future))
            return

        price = future.result()
        c = self._selected_candidate
        self._logger.resolve(
            scan_id=self._scan_id,
            card_id=c["card_id"],
            card_name=c["card_name"],
            set_name=c["set_name"],
            number=c["number"],
            rarity=c.get("rarity"),
            market_price=price.market_price,
        )
        self._on_resolved(
            card_name=c["card_name"],
            set_name=c["set_name"],
            number=c["number"],
            rarity=c.get("rarity"),
            market_price=price.market_price,
        )
        self._executor.shutdown(wait=False)
        self._close()
