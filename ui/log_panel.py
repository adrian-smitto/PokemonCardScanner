import os
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from PIL import Image, ImageTk
from core.state_machine import ScanResult
from core.price_client import VARIANT_ABBREV
import config


def _parse_price(price_str: str) -> float:
    """Parse a price cell string to float for sorting. N/A / … → inf (sorts to bottom)."""
    if not price_str or price_str in ("N/A", "…", "—"):
        return float("inf")
    try:
        return float(price_str.lstrip("$").split()[0])
    except (ValueError, IndexError):
        return float("inf")


def _fmt_holo_cell(variants: list, active: str | None,
                   forced_variant: str | None = None) -> tuple[str, str | None]:
    """Return (cell_text, tag_or_None)."""
    if not variants:
        return "N/A", None
    if forced_variant and active is None:
        abbrev = VARIANT_ABBREV.get(forced_variant, forced_variant)
        return f"? {abbrev}", "holo_warn"
    if active:
        abbrev = VARIANT_ABBREV.get(active, active)
        return abbrev, None
    # Automatic, multiple variants available
    parts = [VARIANT_ABBREV.get(v, v) for v in variants]
    return " · ".join(parts), None


class LogPanel(tk.Frame):
    def __init__(self, parent, on_ambiguous_click=None, on_get_price_tcg=None,
                 on_get_price_pc=None, on_remap=None, on_set_holo_type=None,
                 on_delete=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_ambiguous_click = on_ambiguous_click
        self._on_get_price_tcg = on_get_price_tcg
        self._on_get_price_pc = on_get_price_pc
        self._on_remap = on_remap
        self._on_set_holo_type = on_set_holo_type
        self._on_delete = on_delete
        self._price_sort: str | None = None  # None | "asc" | "desc"
        self._hover_iid: str | None = None
        self._hover_after_id = None
        self._preview_win: tk.Toplevel | None = None
        self._preview_image_ref = None  # keep PhotoImage alive
        self._scan_ids: list[int | None] = []
        self._candidate_counts: list[int] = []
        self._scan_tokens: list[str | None] = []
        self._card_ids: list[str] = []
        self._all_iids: list[str] = []
        self._holo_variants: list[list] = []
        self._holo_warn: list[bool] = []
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

        self._filter_holo_warn_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            filter_bar, text="Variant mismatch",
            variable=self._filter_holo_warn_var,
            command=self._apply_filters,
            bg="#1e1e1e", fg="#ffaa00", selectcolor="#333333",
            activebackground="#1e1e1e", activeforeground="#ffaa00",
        ).pack(side="left", padx=(12, 0))

        cols = ("time", "name", "set", "number", "rarity", "price", "holo", "flags")
        self._tree = ttk.Treeview(self, columns=cols, show="headings", height=10)

        self._tree.heading("time",   text="Time")
        self._tree.heading("name",   text="Card")
        self._tree.heading("set",    text="Set")
        self._tree.heading("number", text="#")
        self._tree.heading("rarity", text="Rarity")
        self._tree.heading("price",  text="Price",
                           command=self._sort_by_price)
        self._tree.heading("holo",   text="Holo")
        self._tree.heading("flags",  text="")

        self._tree.column("time",   width=70,  stretch=False)
        self._tree.column("name",   width=160, stretch=True)
        self._tree.column("set",    width=120, stretch=True)
        self._tree.column("number", width=40,  stretch=False)
        self._tree.column("rarity", width=90,  stretch=False)
        self._tree.column("price",  width=70,  stretch=False)
        self._tree.column("holo",   width=80,  stretch=False)
        self._tree.column("flags",  width=90,  stretch=False)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)

        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._tree.tag_configure("unknown",   foreground="#888888")
        self._tree.tag_configure("holo_warn", foreground="#ffaa00")

        self._tree.bind("<ButtonRelease-1>", self._on_click)
        self._tree.bind("<Double-ButtonRelease-1>", self._on_double_click)
        self._tree.bind("<Button-3>", self._on_right_click)
        self._tree.bind("<Motion>", self._on_hover)
        self._tree.bind("<Leave>", self._on_leave)

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
                    result.number, result.rarity or "", price_str, "", flags),
        )
        self._tree.see(iid)
        self._scan_ids.append(scan_id)
        self._candidate_counts.append(candidate_count)
        self._scan_tokens.append(scan_token)
        self._card_ids.append(result.card_id)
        self._all_iids.append(iid)
        self._holo_variants.append([])
        self._holo_warn.append(False)
        if self._price_sort is not None:
            self._apply_price_sort()

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
        self._tree.item(iid, values=(vals[0], vals[1], vals[2], vals[3], vals[4], price_str, vals[6], vals[7]))

    def update_holo(self, tree_index: int, active_variant: str | None,
                    available_variants: list,
                    forced_variant: str | None = None) -> None:
        if tree_index >= len(self._all_iids):
            return
        iid = self._all_iids[tree_index]
        if tree_index < len(self._holo_variants):
            self._holo_variants[tree_index] = available_variants or []
        text, tag = _fmt_holo_cell(available_variants or [], active_variant, forced_variant)
        vals = list(self._tree.item(iid, "values"))
        vals[6] = text
        existing_tags = self._tree.item(iid, "tags")
        # Preserve non-holo tags (e.g. "unknown"), swap in/out holo_warn
        base_tags = tuple(t for t in existing_tags if t != "holo_warn")
        new_tags = base_tags + ("holo_warn",) if tag == "holo_warn" else base_tags
        self._tree.item(iid, values=vals, tags=new_tags)
        if tree_index < len(self._holo_warn):
            self._holo_warn[tree_index] = (tag == "holo_warn")

    def update_holo_loading(self, tree_index: int, variant: str) -> None:
        if tree_index >= len(self._all_iids):
            return
        iid = self._all_iids[tree_index]
        abbrev = VARIANT_ABBREV.get(variant, variant)
        vals = list(self._tree.item(iid, "values"))
        vals[6] = abbrev
        self._tree.item(iid, values=vals)

    def update_resolved(self, tree_index: int, card_name: str, set_name: str,
                        number: str, rarity: str | None, market_price: float | None) -> None:
        """Update a row after manual resolution."""
        if tree_index >= len(self._all_iids):
            return
        iid = self._all_iids[tree_index]
        price_str = f"${market_price:.2f}" if market_price is not None else "N/A"
        vals = self._tree.item(iid, "values")
        self._tree.item(iid, values=(
            vals[0], card_name, set_name, number, rarity or "", price_str, vals[6], "corrected"
        ))

    def clear(self) -> None:
        self._cancel_preview()
        self._hover_iid = None
        for iid in self._all_iids:
            self._tree.delete(iid)
        self._scan_ids = []
        self._candidate_counts = []
        self._scan_tokens = []
        self._card_ids = []
        self._all_iids = []
        self._holo_variants = []
        self._holo_warn = []
        self._filter_unknown_var.set(False)
        self._filter_alts_var.set(False)
        self._filter_holo_warn_var.set(False)
        self._price_sort = None
        self._tree.heading("price", text="Price")
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

        variants = self._holo_variants[idx] if idx < len(self._holo_variants) else []
        if variants and self._on_set_holo_type:
            holo_menu = tk.Menu(menu, tearoff=0)
            for v in variants:
                abbrev = VARIANT_ABBREV.get(v, v)
                holo_menu.add_command(
                    label=abbrev,
                    command=lambda vv=v: self._on_set_holo_type(scan_id, idx, vv),
                )
            menu.add_cascade(label="Set Holo Type", menu=holo_menu)

        menu.add_separator()
        menu.add_command(
            label="Delete Row",
            command=lambda: self._on_delete(scan_id, idx) if self._on_delete else None,
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
        self._tree.item(iid, values=(vals[0], vals[1], vals[2], vals[3], vals[4], "…", "…", vals[7]))

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

            # Restore holo data if present in session
            holo_type = row.get("holo_type")
            import json as _json
            holo_variants_raw = row.get("holo_variants")
            if isinstance(holo_variants_raw, str):
                try:
                    avail = _json.loads(holo_variants_raw)
                except Exception:
                    avail = []
            elif isinstance(holo_variants_raw, list):
                avail = holo_variants_raw
            else:
                avail = []
            holo_text, _ = _fmt_holo_cell(avail, holo_type)

            iid = self._tree.insert(
                "", "end",
                tags=("unknown",) if is_unknown else (),
                values=(ts, row.get("card_name", ""), row.get("set_name", ""),
                        row.get("number", ""), row.get("rarity") or "",
                        price_str, holo_text, flags),
            )
            self._tree.see(iid)
            self._scan_ids.append(row.get("id"))
            self._candidate_counts.append(0)
            self._scan_tokens.append(row.get("scan_token"))
            self._card_ids.append(card_id)
            self._all_iids.append(iid)
            self._holo_variants.append(avail)
            self._holo_warn.append(holo_text.startswith("?"))
        if not rows:
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _on_hover(self, event) -> None:
        iid = self._tree.identify_row(event.y)
        if iid == self._hover_iid:
            return
        # Row changed — cancel pending timer and hide any existing popup
        self._cancel_preview()
        self._hover_iid = iid
        if not iid:
            return
        # Find card_id for this row
        try:
            idx = self._all_iids.index(iid)
        except ValueError:
            return
        card_id = self._card_ids[idx] if idx < len(self._card_ids) else ""
        if not card_id:
            return  # unknown card — no image
        # Schedule popup after 1 second
        x_root, y_root = event.x_root, event.y_root
        self._hover_after_id = self._tree.after(
            1000, lambda: self._show_preview(card_id, x_root, y_root)
        )

    def _on_leave(self, _event=None) -> None:
        self._cancel_preview()
        self._hover_iid = None

    def _cancel_preview(self) -> None:
        if self._hover_after_id is not None:
            self._tree.after_cancel(self._hover_after_id)
            self._hover_after_id = None
        self._destroy_preview()

    def _destroy_preview(self) -> None:
        if self._preview_win is not None:
            try:
                self._preview_win.destroy()
            except Exception:
                pass
            self._preview_win = None
            self._preview_image_ref = None

    def _show_preview(self, card_id: str, x_root: int, y_root: int) -> None:
        self._destroy_preview()
        safe_id = card_id.replace("/", "_")
        path = os.path.join(config.IMAGES_DIR, f"{safe_id}.jpg")
        if not os.path.exists(path):
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((200, 280), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
        except Exception:
            return
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"+{x_root + 16}+{y_root}")
        tk.Label(win, image=photo, bd=0).pack()
        self._preview_win = win
        self._preview_image_ref = photo  # prevent GC

    def delete_row(self, tree_index: int) -> None:
        if tree_index >= len(self._all_iids):
            return
        iid = self._all_iids[tree_index]
        self._tree.delete(iid)
        del self._all_iids[tree_index]
        del self._scan_ids[tree_index]
        del self._candidate_counts[tree_index]
        del self._scan_tokens[tree_index]
        del self._card_ids[tree_index]
        del self._holo_variants[tree_index]
        del self._holo_warn[tree_index]
        if not self._all_iids:
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _sort_by_price(self) -> None:
        if self._price_sort is None:
            self._price_sort = "asc"
        elif self._price_sort == "asc":
            self._price_sort = "desc"
        else:
            self._price_sort = None
        labels = {None: "Price", "asc": "Price ▲", "desc": "Price ▼"}
        self._tree.heading("price", text=labels[self._price_sort])
        self._apply_price_sort()

    def _apply_price_sort(self) -> None:
        if self._price_sort is None:
            # Restore natural insertion order
            for pos, iid in enumerate(self._all_iids):
                self._tree.move(iid, "", pos)
            return
        reverse = (self._price_sort == "desc")
        sorted_iids = sorted(
            self._all_iids,
            key=lambda iid: _parse_price(self._tree.item(iid, "values")[5]),
            reverse=reverse,
        )
        for pos, iid in enumerate(sorted_iids):
            self._tree.move(iid, "", pos)

    def _apply_filters(self) -> None:
        want_unknown    = self._filter_unknown_var.get()
        want_alts       = self._filter_alts_var.get()
        want_holo_warn  = self._filter_holo_warn_var.get()
        for i, iid in enumerate(self._all_iids):
            is_unknown  = i < len(self._card_ids) and self._card_ids[i] == ""
            has_alts    = i < len(self._candidate_counts) and self._candidate_counts[i] > 0
            is_holo_warn = i < len(self._holo_warn) and self._holo_warn[i]
            show = True
            if want_unknown and not is_unknown:
                show = False
            if want_alts and not has_alts:
                show = False
            if want_holo_warn and not is_holo_warn:
                show = False
            if show:
                self._tree.reattach(iid, "", "end")
            else:
                self._tree.detach(iid)
        if self._price_sort is not None:
            self._apply_price_sort()
