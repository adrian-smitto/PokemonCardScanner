import tkinter as tk
from tkinter import ttk
from datetime import datetime
from core.state_machine import ScanResult


class LogPanel(tk.Frame):
    def __init__(self, parent, on_ambiguous_click=None, on_get_price_tcg=None,
                 on_get_price_pc=None, on_remap=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_ambiguous_click = on_ambiguous_click
        self._on_get_price_tcg = on_get_price_tcg
        self._on_get_price_pc = on_get_price_pc
        self._on_remap = on_remap
        self._scan_ids: list[int | None] = []
        self._candidate_counts: list[int] = []
        self._scan_tokens: list[str | None] = []
        self._card_ids: list[str] = []
        self._all_iids: list[str] = []
        self._build()

    def _build(self) -> None:
        # Filter bar
        filter_bar = tk.Frame(self, bg="#1e1e1e")
        filter_bar.pack(fill="x", padx=4, pady=(4, 2))

        self._filter_unknown_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            filter_bar, text="Unknown only",
            variable=self._filter_unknown_var,
            command=self._apply_filters,
            bg="#1e1e1e", fg="#aaaaaa", selectcolor="#333333",
            activebackground="#1e1e1e", activeforeground="white",
        ).pack(side="left", padx=(0, 12))

        self._filter_alts_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            filter_bar, text="Has alts",
            variable=self._filter_alts_var,
            command=self._apply_filters,
            bg="#1e1e1e", fg="#aaaaaa", selectcolor="#333333",
            activebackground="#1e1e1e", activeforeground="white",
        ).pack(side="left")

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

        self._tree.tag_configure("unknown", foreground="#888888")

        self._tree.bind("<ButtonRelease-1>", self._on_click)
        self._tree.bind("<Double-ButtonRelease-1>", self._on_double_click)
        self._tree.bind("<Button-3>", self._on_right_click)

        self._empty_label = tk.Label(self, text="No cards scanned yet",
                                     fg="#888888", bg="white")
        self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def append(self, result: ScanResult, scan_id: int | None, candidate_count: int,
               *, scan_token: str | None = None) -> None:
        self._empty_label.place_forget()

        time_str = datetime.now().strftime("%H:%M:%S")
        price_str = f"${result.market_price:.2f}" if result.market_price is not None else "N/A"
        flags = ""
        if candidate_count > 0:
            flags = f"? {candidate_count} alt{'s' if candidate_count > 1 else ''}"

        is_unknown = result.card_id == ""
        iid = self._tree.insert(
            "", "end",
            tags=("unknown",) if is_unknown else (),
            values=(time_str, result.card_name, result.set_name,
                    result.number, result.rarity or "", price_str, flags),
        )
        self._tree.see(iid)
        self._scan_ids.append(scan_id)
        self._candidate_counts.append(candidate_count)
        self._scan_tokens.append(scan_token)
        self._card_ids.append(result.card_id)
        self._all_iids.append(iid)

    def update_price(self, tree_index: int, market_price: float | None,
                     source: str | None = None) -> None:
        """Backfill the price cell once the background fetch completes."""
        if tree_index >= len(self._all_iids):
            return
        iid = self._all_iids[tree_index]
        if market_price is not None:
            price_str = f"${market_price:.2f} {source}" if source else f"${market_price:.2f}"
        else:
            price_str = "N/A"
        vals = self._tree.item(iid, "values")
        self._tree.item(iid, values=(vals[0], vals[1], vals[2], vals[3], vals[4], price_str, vals[6]))

    def update_resolved(self, tree_index: int, card_name: str, set_name: str,
                        number: str, rarity: str | None, market_price: float | None) -> None:
        """Update a row after manual resolution."""
        if tree_index >= len(self._all_iids):
            return
        iid = self._all_iids[tree_index]
        price_str = f"${market_price:.2f}" if market_price is not None else "N/A"
        vals = self._tree.item(iid, "values")
        self._tree.item(iid, values=(
            vals[0], card_name, set_name, number, rarity or "", price_str, "corrected"
        ))

    def clear(self) -> None:
        for iid in self._all_iids:
            self._tree.delete(iid)
        self._scan_ids = []
        self._candidate_counts = []
        self._scan_tokens = []
        self._card_ids = []
        self._all_iids = []
        self._filter_unknown_var.set(False)
        self._filter_alts_var.set(False)
        self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _on_double_click(self, event) -> None:
        item = self._tree.identify_row(event.y)
        if not item:
            return
        idx = self._all_iids.index(item)
        if idx < len(self._card_ids) and self._card_ids[idx] == "":
            scan_id = self._scan_ids[idx] if idx < len(self._scan_ids) else None
            scan_token = self._scan_tokens[idx] if idx < len(self._scan_tokens) else None
            if self._on_remap:
                self._on_remap(scan_id, scan_token, idx)

    def _on_click(self, event) -> None:
        if not self._on_ambiguous_click:
            return
        item = self._tree.identify_row(event.y)
        if not item:
            return
        idx = self._all_iids.index(item)
        if idx < len(self._candidate_counts) and self._candidate_counts[idx] > 0:
            scan_id = self._scan_ids[idx]
            self._on_ambiguous_click(scan_id, idx)

    def _on_right_click(self, event) -> None:
        item = self._tree.identify_row(event.y)
        if not item:
            return
        self._tree.selection_set(item)
        idx = self._all_iids.index(item)
        scan_id = self._scan_ids[idx] if idx < len(self._scan_ids) else None
        scan_token = self._scan_tokens[idx] if idx < len(self._scan_tokens) else None

        import config
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="Get Price (TCGPlayer)",
            command=lambda: self._on_get_price_tcg(scan_id, idx) if self._on_get_price_tcg else None,
        )
        if config.PRICECHARTING_ENABLED:
            menu.add_command(
                label="Get Price (PriceCharting)",
                command=lambda: self._on_get_price_pc(scan_id, idx) if self._on_get_price_pc else None,
            )
        menu.add_command(
            label="Remap Card",
            command=lambda: self._on_remap(scan_id, scan_token, idx) if self._on_remap else None,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def get_unpriced_rows(self) -> list[tuple[int, int]]:
        """Return (scan_id, tree_index) for every row currently showing N/A."""
        result = []
        for idx, iid in enumerate(self._all_iids):
            vals = self._tree.item(iid, "values")
            price_str = vals[5] if len(vals) > 5 else ""
            if price_str == "N/A" and idx < len(self._scan_ids) and self._scan_ids[idx] is not None:
                result.append((self._scan_ids[idx], idx))
        return result

    def update_price_loading(self, tree_index: int) -> None:
        if tree_index >= len(self._all_iids):
            return
        iid = self._all_iids[tree_index]
        vals = self._tree.item(iid, "values")
        self._tree.item(iid, values=(vals[0], vals[1], vals[2], vals[3], vals[4], "…", vals[6]))

    def load_session(self, rows: list[dict]) -> None:
        """Populate the log from a saved session (no live ScanResult objects)."""
        self.clear()
        self._empty_label.place_forget()
        for row in rows:
            card_id = row.get("card_id") or ""
            is_unknown = card_id == ""
            price = row.get("market_price")
            source = row.get("price_source")
            if price is not None:
                price_str = f"${price:.2f} {source}" if source else f"${price:.2f}"
            else:
                price_str = "N/A"
            flags = "corrected" if row.get("is_corrected") else ""
            ts = row.get("scanned_at", "")[:19].replace("T", " ")[-8:]  # HH:MM:SS
            iid = self._tree.insert(
                "", "end",
                tags=("unknown",) if is_unknown else (),
                values=(ts, row.get("card_name", ""), row.get("set_name", ""),
                        row.get("number", ""), row.get("rarity") or "",
                        price_str, flags),
            )
            self._tree.see(iid)
            self._scan_ids.append(row.get("id"))
            self._candidate_counts.append(0)
            self._scan_tokens.append(row.get("scan_token"))
            self._card_ids.append(card_id)
            self._all_iids.append(iid)
        if not rows:
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _apply_filters(self) -> None:
        want_unknown = self._filter_unknown_var.get()
        want_alts    = self._filter_alts_var.get()
        for i, iid in enumerate(self._all_iids):
            is_unknown = i < len(self._card_ids) and self._card_ids[i] == ""
            has_alts   = i < len(self._candidate_counts) and self._candidate_counts[i] > 0
            show = True
            if want_unknown and not is_unknown:
                show = False
            if want_alts and not has_alts:
                show = False
            if show:
                self._tree.reattach(iid, "", "end")
            else:
                self._tree.detach(iid)
