import os
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

import config
from core.hasher import compute_phash
from core.matcher import CardMatcher, CardCandidate


class RemapDialog:
    def __init__(self, parent, scan_id: int, scan_token: str | None,
                 matcher: CardMatcher, on_resolved, remap_n: int = 100):
        """
        parent      — parent Tk widget
        scan_id     — scan_log row id
        scan_token  — UUID used to find the capture image
        matcher     — CardMatcher instance (for find_top_n)
        on_resolved — callback(scan_id, card_id, card_name, set_name, number, rarity)
        remap_n     — how many top candidates to show
        """
        self._scan_id = scan_id
        self._scan_token = scan_token
        self._matcher = matcher
        self._on_resolved = on_resolved
        self._remap_n = remap_n
        self._candidates: list[CardCandidate] = []
        self._all_iids: list[str] = []
        self._selected_idx: int | None = None
        self._thumb_cache: list[ImageTk.PhotoImage] = []  # keep refs alive

        self._win = tk.Toplevel(parent)
        self._win.title("Remap Card")
        self._win.configure(bg="#1e1e1e")
        self._win.resizable(True, True)
        self._win.grab_set()

        self._build()
        self._load_capture()
        self._load_candidates()

    def _build(self) -> None:
        # Top section: capture image + N control
        top = tk.Frame(self._win, bg="#1e1e1e")
        top.pack(fill="x", padx=10, pady=8)

        # Capture image (left)
        self._capture_label = tk.Label(
            top, bg="#333333", width=15, height=10,
            text="No capture", fg="#888888",
        )
        self._capture_label.pack(side="left", padx=(0, 4))

        # Arrow
        tk.Label(top, text="→", font=("Helvetica", 14),
                 bg="#1e1e1e", fg="#888888").pack(side="left", padx=4)

        # Selected card preview (updates on row click)
        self._selected_label = tk.Label(
            top, bg="#333333", width=15, height=10,
            text="Select a card", fg="#888888",
        )
        self._selected_label.pack(side="left", padx=(4, 10))

        # N slider (right of previews)
        ctrl = tk.Frame(top, bg="#1e1e1e")
        ctrl.pack(side="left", fill="y")

        tk.Label(ctrl, text="Show top N matches:", fg="#aaaaaa",
                 bg="#1e1e1e", font=("Helvetica", 9)).pack(anchor="w")
        self._n_var = tk.IntVar(value=self._remap_n)
        tk.Scale(
            ctrl, from_=10, to=500, orient="horizontal",
            variable=self._n_var, length=180, bg="#1e1e1e",
            fg="white", troughcolor="#333333", highlightthickness=0,
        ).pack(anchor="w")
        tk.Button(
            ctrl, text="Refresh", command=self._load_candidates,
            font=("Helvetica", 9),
        ).pack(anchor="w", pady=(4, 0))

        # Candidate list
        list_frame = tk.Frame(self._win, bg="#1e1e1e")
        list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # Filter bar
        filter_bar = tk.Frame(list_frame, bg="#1e1e1e")
        filter_bar.pack(fill="x", pady=(0, 4))
        tk.Label(filter_bar, text="Filter:", fg="#aaaaaa",
                 bg="#1e1e1e", font=("Helvetica", 9)).pack(side="left", padx=(0, 4))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", self._on_filter_change)
        tk.Entry(filter_bar, textvariable=self._filter_var,
                 bg="#333333", fg="white", insertbackground="white",
                 relief="flat").pack(side="left", fill="x", expand=True)
        tk.Button(filter_bar, text="✕", font=("Helvetica", 9),
                  command=lambda: self._filter_var.set("")).pack(side="left", padx=(4, 0))

        cols = ("thumb", "name", "set", "number", "rarity", "dist")
        self._tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=14)
        self._tree.heading("thumb",  text="")
        self._tree.heading("name",   text="Card")
        self._tree.heading("set",    text="Set")
        self._tree.heading("number", text="#")
        self._tree.heading("rarity", text="Rarity")
        self._tree.heading("dist",   text="Dist")

        self._tree.column("thumb",  width=50,  stretch=False)
        self._tree.column("name",   width=180, stretch=True)
        self._tree.column("set",    width=130, stretch=True)
        self._tree.column("number", width=40,  stretch=False)
        self._tree.column("rarity", width=90,  stretch=False)
        self._tree.column("dist",   width=40,  stretch=False)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        # Buttons
        btn_bar = tk.Frame(self._win, bg="#1e1e1e")
        btn_bar.pack(fill="x", padx=10, pady=(0, 10))

        self._confirm_btn = tk.Button(
            btn_bar, text="Confirm", command=self._confirm,
            state="disabled", font=("Helvetica", 9, "bold"),
            bg="#1b5e20", fg="white", activebackground="#2e7d32",
        )
        self._confirm_btn.pack(side="right", padx=(4, 0))
        tk.Button(
            btn_bar, text="Cancel", command=self._win.destroy,
            font=("Helvetica", 9),
        ).pack(side="right")

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
            self._capture_label.config(image=photo, width=img.width, height=img.height, text="")
            self._capture_label.image = photo  # keep reference
        except Exception:
            pass

    def _load_candidates(self) -> None:
        if not self._scan_token:
            self._candidates = []
            self._refresh_list()
            return

        path = os.path.join(config.CAPTURES_DIR, f"{self._scan_token}.jpg")
        if not os.path.exists(path):
            self._candidates = []
            self._refresh_list()
            return

        try:
            img = Image.open(path).convert("RGB")
            query_hash = compute_phash(img)
            n = self._n_var.get()
            self._candidates = self._matcher.find_top_n(query_hash, n)
        except Exception:
            self._candidates = []

        self._refresh_list()

    def _refresh_list(self) -> None:
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._thumb_cache.clear()
        self._all_iids.clear()
        self._selected_idx = None
        self._confirm_btn.config(state="disabled")
        self._filter_var.set("")

        for card in self._candidates:
            thumb_photo = self._load_thumb(card.card_id)
            iid = self._tree.insert("", "end", image=thumb_photo, values=(
                "", card.name, card.set_name, card.number,
                card.rarity or "", card.hamming_dist,
            ))
            if thumb_photo:
                self._tree.item(iid, image=thumb_photo)
            self._thumb_cache.append(thumb_photo)
            self._all_iids.append(iid)

    def _on_filter_change(self, *_) -> None:
        query = self._filter_var.get().lower()
        for iid in self._all_iids:
            name = self._tree.item(iid, "values")[1].lower()
            if query in name:
                self._tree.reattach(iid, "", "end")
            else:
                self._tree.detach(iid)

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
            self._selected_idx = None
            self._confirm_btn.config(state="disabled")
            return
        try:
            self._selected_idx = self._all_iids.index(sel[0])
        except ValueError:
            return
        self._confirm_btn.config(state="normal")
        card = self._candidates[self._selected_idx]
        self._show_selected_preview(card.card_id)

    def _show_selected_preview(self, card_id: str) -> None:
        safe_id = card_id.replace("/", "_")
        path = os.path.join(config.IMAGES_DIR, f"{safe_id}.jpg")
        if not os.path.exists(path):
            self._selected_label.config(image="", text="No image", width=15, height=10)
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((200, 280), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._selected_label.config(
                image=photo, text="", width=img.width, height=img.height)
            self._selected_label.image = photo
        except Exception:
            self._selected_label.config(image="", text="No image", width=15, height=10)

    def _confirm(self) -> None:
        if self._selected_idx is None or self._selected_idx >= len(self._candidates):
            return
        card = self._candidates[self._selected_idx]
        self._on_resolved(
            self._scan_id,
            card.card_id, card.name, card.set_name,
            card.number, card.rarity,
        )
        self._win.destroy()
