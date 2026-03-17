import tkinter as tk
from tkinter import ttk
from concurrent.futures import ThreadPoolExecutor

from core.price_client import PriceClient
from core.scan_log import ScanLogger


class ResolutionDialog(tk.Toplevel):
    """
    Modal dialog for resolving an ambiguous scan.
    Lets the user pick the correct card from the saved candidates.
    Fetches the price on demand when a selection is confirmed.
    """

    def __init__(self, parent, scan_id: int, logger: ScanLogger,
                 price_client: PriceClient, on_resolved, **kwargs):
        super().__init__(parent, **kwargs)
        self.title("Resolve Ambiguous Scan")
        self.resizable(False, False)
        self.grab_set()  # modal

        self._scan_id = scan_id
        self._logger = logger
        self._price_client = price_client
        self._on_resolved = on_resolved
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._selected_candidate = None

        candidates = logger.get_candidates(scan_id)
        self._candidates = [dict(row) for row in candidates]

        self._build()
        self.transient(parent)
        self.wait_visibility()
        self.grab_set()

    def _build(self) -> None:
        tk.Label(self, text="Select the correct card:", font=("Helvetica", 11, "bold"),
                 pady=10).pack(padx=20, anchor="w")

        cols = ("name", "set", "number", "rarity", "dist")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=min(len(self._candidates), 8))
        self._tree.heading("name",   text="Card Name")
        self._tree.heading("set",    text="Set")
        self._tree.heading("number", text="#")
        self._tree.heading("rarity", text="Rarity")
        self._tree.heading("dist",   text="Confidence")

        self._tree.column("name",   width=200, stretch=True)
        self._tree.column("set",    width=130, stretch=True)
        self._tree.column("number", width=40,  stretch=False)
        self._tree.column("rarity", width=90,  stretch=False)
        self._tree.column("dist",   width=80,  stretch=False)

        for c in self._candidates:
            dist = c["hamming_dist"]
            confidence = f"{max(0, 100 - dist * 3)}%"
            self._tree.insert("", "end", values=(
                c["card_name"], c["set_name"], c["number"],
                c.get("rarity") or "", confidence,
            ))

        self._tree.pack(padx=20, pady=5, fill="both", expand=True)

        btn_frame = tk.Frame(self)
        btn_frame.pack(padx=20, pady=10, fill="x")

        self._confirm_btn = tk.Button(btn_frame, text="Confirm", width=12,
                                      command=self._confirm, state="disabled")
        self._confirm_btn.pack(side="right", padx=(5, 0))

        tk.Button(btn_frame, text="Cancel", width=12,
                  command=self.destroy).pack(side="right")

        self._status_var = tk.StringVar(value="Select a card to confirm.")
        tk.Label(self, textvariable=self._status_var, fg="#888888",
                 font=("Helvetica", 9, "italic")).pack(padx=20, pady=(0, 10))

        self._tree.bind("<<TreeviewSelect>>", self._on_select)

    def _on_select(self, _event) -> None:
        sel = self._tree.selection()
        if sel:
            idx = self._tree.index(sel[0])
            self._selected_candidate = self._candidates[idx]
            self._confirm_btn.config(state="normal")
            self._status_var.set(f"Selected: {self._selected_candidate['card_name']}")

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
        self.destroy()
