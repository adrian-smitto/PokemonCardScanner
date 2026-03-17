import tkinter as tk
from tkinter import ttk
from datetime import datetime
from core.state_machine import ScanResult


class LogPanel(tk.Frame):
    def __init__(self, parent, on_ambiguous_click=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_ambiguous_click = on_ambiguous_click
        self._scan_ids: list[int | None] = []   # parallel to tree rows
        self._candidate_counts: list[int] = []
        self._build()

    def _build(self) -> None:
        cols = ("time", "name", "set", "number", "rarity", "price", "flags")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=10)

        self._tree.heading("time",   text="Time")
        self._tree.heading("name",   text="Card")
        self._tree.heading("set",    text="Set")
        self._tree.heading("number", text="#")
        self._tree.heading("rarity", text="Rarity")
        self._tree.heading("price",  text="Price")
        self._tree.heading("flags",  text="")

        self._tree.column("time",   width=70,  stretch=False)
        self._tree.column("name",   width=160, stretch=True)
        self._tree.column("set",    width=120, stretch=True)
        self._tree.column("number", width=40,  stretch=False)
        self._tree.column("rarity", width=90,  stretch=False)
        self._tree.column("price",  width=70,  stretch=False)
        self._tree.column("flags",  width=90,  stretch=False)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)

        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._tree.bind("<ButtonRelease-1>", self._on_click)

        self._empty_label = tk.Label(self, text="No cards scanned yet",
                                     fg="#888888", bg="white")
        self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def append(self, result: ScanResult, scan_id: int | None, candidate_count: int) -> None:
        self._empty_label.place_forget()

        time_str = datetime.now().strftime("%H:%M:%S")
        price_str = f"${result.market_price:.2f}" if result.market_price is not None else "N/A"
        flags = ""
        if candidate_count > 0:
            flags = f"? {candidate_count} alt{'s' if candidate_count > 1 else ''}"

        iid = self._tree.insert(
            "", "end",
            values=(time_str, result.card_name, result.set_name,
                    result.number, result.rarity or "", price_str, flags),
        )
        self._tree.see(iid)
        self._scan_ids.append(scan_id)
        self._candidate_counts.append(candidate_count)

    def update_price(self, tree_index: int, market_price: float | None) -> None:
        """Backfill the price cell once the background fetch completes."""
        items = self._tree.get_children()
        if tree_index >= len(items):
            return
        iid = items[tree_index]
        price_str = f"${market_price:.2f}" if market_price is not None else "N/A"
        vals = self._tree.item(iid, "values")
        self._tree.item(iid, values=(vals[0], vals[1], vals[2], vals[3], vals[4], price_str, vals[6]))

    def update_resolved(self, tree_index: int, card_name: str, set_name: str,
                        number: str, rarity: str | None, market_price: float | None) -> None:
        """Update a row after manual resolution."""
        items = self._tree.get_children()
        if tree_index >= len(items):
            return
        iid = items[tree_index]
        price_str = f"${market_price:.2f}" if market_price is not None else "N/A"
        vals = self._tree.item(iid, "values")
        self._tree.item(iid, values=(
            vals[0], card_name, set_name, number, rarity or "", price_str, "corrected"
        ))

    def clear(self) -> None:
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._scan_ids = []
        self._candidate_counts = []
        self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _on_click(self, event) -> None:
        if not self._on_ambiguous_click:
            return
        item = self._tree.identify_row(event.y)
        if not item:
            return
        items = list(self._tree.get_children())
        idx = items.index(item)
        if idx < len(self._candidate_counts) and self._candidate_counts[idx] > 0:
            scan_id = self._scan_ids[idx]
            self._on_ambiguous_click(scan_id, idx)
