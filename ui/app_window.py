import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor
import uuid

import config
from core.camera import CameraCapture
from core.state_machine import ScanStateMachine, ScanResult, PriceUpdate
from core.scan_log import ScanLogger, ScanRecord
from core.price_client import PriceClient, PriceResult
from core.pricecharting_client import PriceChartingClient
from core.roi import ROI, load_roi, save_roi, load_setting, save_setting, is_on_screen
from ui.feed_panel import FeedPanel
from ui.result_panel import ResultPanel
from ui.log_panel import LogPanel
from ui.debug_log import DebugLog
from ui.resolution_dialog import ResolutionDialog
from ui.remap_dialog import RemapDialog
from ui.snip_overlay import SnipOverlay
from PIL import ImageGrab


def _fetch_tcg_with_retry(price_client, card_id: str, max_retries: int = 3):
    """Fetch TCGPlayer price, retrying up to max_retries times on failure."""
    result = None
    for attempt in range(max_retries):
        result = price_client.fetch_price(card_id)
        if result.available:
            return result
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    return result


class AppWindow:
    def __init__(self):
        self._session_id = str(uuid.uuid4())
        self._scan_count = 0
        self._total_value = 0.0
        self._unpriced_count = 0
        # scan_token → (scan_id, tree_index) for backfilling prices
        self._pending_price_map: dict[str, tuple[int, int]] = {}

        # Apply persisted settings before building UI so sliders initialise correctly
        config.DIGITAL_ZOOM_DEFAULT = load_setting("zoom", config.DIGITAL_ZOOM_DEFAULT)
        config.MATCH_HAMMING_THRESHOLD = load_setting("max_dist", config.MATCH_HAMMING_THRESHOLD)
        self._current_zoom: float = config.DIGITAL_ZOOM_DEFAULT

        self._root = tk.Tk()
        self._root.title("Pokemon Card Scanner")
        self._root.configure(bg="#121212")
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        geo = load_setting("main_geometry", None)
        if geo and is_on_screen(geo):
            self._root.geometry(geo)

        self._camera = CameraCapture()
        self._logger = ScanLogger()
        self._price_client = PriceClient()
        self._pc_client = PriceChartingClient() if config.PRICECHARTING_ENABLED else None
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._state_machine: ScanStateMachine | None = None
        self._manual_price_futures: list = []
        self._bulk_pending: int = 0
        self._snip_futures: list = []   # (future, scan_token)

        self._build_ui()
        self._startup()

    def _build_ui(self) -> None:
        top = tk.Frame(self._root, bg="#121212")
        top.pack(fill="both", expand=True, padx=10, pady=10)

        self._feed = FeedPanel(
            top,
            on_zoom_change=self._on_zoom_change,
            on_roi_change=self._on_roi_change,
            bg="black",
        )
        self._feed.pack(side="left", fill="both", expand=False)

        self._result = ResultPanel(top)
        self._result.pack(side="left", fill="both", expand=True, padx=(10, 0))

        self._log = LogPanel(
            self._root,
            on_ambiguous_click=self._open_resolution,
            on_get_price_tcg=self._on_get_price_tcg,
            on_get_price_pc=self._on_get_price_pc,
            on_remap=self._on_remap,
            bg="white",
        )
        self._log.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        self._debug = DebugLog(self._root)
        self._debug.pack(fill="x", padx=10, pady=(0, 5))

        status_bar = tk.Frame(self._root, bg="#1e1e1e")
        status_bar.pack(fill="x", padx=10, pady=(0, 10))

        # Row 1 — buttons
        btn_row = tk.Frame(status_bar, bg="#1e1e1e")
        btn_row.pack(fill="x")

        self._scanning = False
        self._scan_btn = tk.Button(
            btn_row, text="▶  Enable Scanning", width=18,
            font=("Helvetica", 9, "bold"),
            bg="#333333", fg="#aaaaaa",
            activebackground="#444444",
            command=self._toggle_scanning,
        )
        self._scan_btn.pack(side="left", padx=(0, 6), pady=4)

        tk.Button(btn_row, text="Save Session", command=self._save_session,
                  font=("Helvetica", 9)).pack(side="left", padx=(0, 4), pady=4)
        tk.Button(btn_row, text="Load Session", command=self._load_session,
                  font=("Helvetica", 9)).pack(side="left", padx=(0, 4), pady=4)
        tk.Button(btn_row, text="Fetch Missing Prices",
                  command=self._fetch_missing_prices,
                  font=("Helvetica", 9)).pack(side="left", padx=(0, 4), pady=4)
        tk.Button(btn_row, text="Export CSV", command=self._export_csv,
                  font=("Helvetica", 9)).pack(side="left", padx=(0, 4), pady=4)

        # Row 2 — status text + bulk fetch indicator
        info_row = tk.Frame(status_bar, bg="#1e1e1e")
        info_row.pack(fill="x")

        self._status_var = tk.StringVar(value="0 cards — $0.00")
        tk.Label(info_row, textvariable=self._status_var, fg="#aaaaaa",
                 bg="#1e1e1e", font=("Helvetica", 10)).pack(side="left", padx=(10, 0), pady=(0, 4))

        self._bulk_var = tk.StringVar()
        self._bulk_label = tk.Label(info_row, textvariable=self._bulk_var,
                                    fg="#ffcc00", bg="#1e1e1e", font=("Helvetica", 9))
        # shown/hidden dynamically — not packed yet

        shortcut_display = config.SNIP_HOTKEY.strip("<>").replace("-", "+")
        self._snip_var = tk.StringVar(value=f"Snip: {shortcut_display}")
        self._snip_label = tk.Label(
            info_row, textvariable=self._snip_var,
            fg="#888888", bg="#1e1e1e", font=("Helvetica", 8),
        )
        self._snip_label.pack(side="right", padx=(0, 10))

    def _startup(self) -> None:
        ok = self._camera.start()
        if self._current_zoom != 1.0:
            self._camera.digital_zoom = self._current_zoom
        if not ok:
            self._feed.show_error(self._camera.error or "Camera error")
            return

        if not os.path.exists(config.DB_PATH):
            self._status_var.set("cards.db not found — run  python build_db.py  first. Camera active.")
            self._root.after(config.UI_TICK_MS, self._tick_camera_only)
            return

        self._state_machine = ScanStateMachine(
            self._session_id, on_status=self._debug.log, pc_client=self._pc_client
        )

        # Restore saved ROI
        roi = load_roi()
        if roi:
            self._state_machine.roi = roi
            self._feed.set_roi_display(roi)
            self._debug.log("Scan area restored from settings", "dim")

        self._root.bind(config.SNIP_HOTKEY, self._on_snip_hotkey)
        self._debug.log("Ready — press Enable Scanning to start", "info")
        self._root.after(config.UI_TICK_MS, self._tick)

    def _tick_camera_only(self) -> None:
        frame = self._camera.latest_frame
        if frame is not None:
            self._feed.update_frame(frame)
        self._root.after(config.UI_TICK_MS, self._tick_camera_only)

    def _tick(self) -> None:
        frame = self._camera.latest_frame
        if frame is not None:
            if self._scanning:
                annotated, _ = self._state_machine.process(frame)
                self._feed.update_frame(annotated)
            else:
                self._feed.update_frame(frame)

        self._drain_manual_prices()
        self._drain_snip_futures()

        while not self._state_machine.result_queue.empty():
            result: ScanResult = self._state_machine.result_queue.get_nowait()
            self._handle_result(result)

        while not self._state_machine.price_update_queue.empty():
            update: PriceUpdate = self._state_machine.price_update_queue.get_nowait()
            self._handle_price_update(update)

        self._root.after(config.UI_TICK_MS, self._tick)

    def _toggle_scanning(self) -> None:
        self._scanning = not self._scanning
        if self._scanning:
            self._scan_btn.config(text="⏹  Disable Scanning", bg="#1b5e20", fg="white")
            self._snip_label.config(fg="#555555")
            self._debug.log("Scanning enabled", "success")
        else:
            self._scan_btn.config(text="▶  Enable Scanning", bg="#333333", fg="#aaaaaa")
            self._snip_label.config(fg="#888888")
            self._debug.log("Scanning disabled", "dim")

    def _handle_result(self, result: ScanResult) -> None:
        record = ScanRecord(
            session_id=result.session_id,
            card_id=result.card_id,
            card_name=result.card_name,
            set_name=result.set_name,
            number=result.number,
            rarity=result.rarity,
            market_price=None,   # price arrives later via price_update_queue
            hamming_dist=result.hamming_dist,
            candidates=result.candidates,
            scan_token=result.scan_token,
        )
        scan_id = self._logger.log_scan(record)
        tree_index = self._scan_count   # row index before incrementing
        self._log.append(result, scan_id, len(result.candidates),
                         scan_token=result.scan_token)
        self._result.display(result)

        if result.card_id:
            # Known card — kick off background price fetch
            self._pending_price_map[result.scan_token] = (scan_id, tree_index)

        self._scan_count += 1
        self._unpriced_count += 1
        self._update_status()

    def _handle_price_update(self, update: PriceUpdate) -> None:
        entry = self._pending_price_map.pop(update.scan_token, None)
        if entry is None:
            return
        scan_id, tree_index = entry

        self._logger.update_price(scan_id, update.market_price, update.price_source)
        self._log.update_price(tree_index, update.market_price, update.price_source)

        self._unpriced_count -= 1
        if update.market_price is not None:
            self._total_value += update.market_price
        else:
            self._unpriced_count += 1  # still unpriced — API had no data

        self._update_status()

    def _update_status(self) -> None:
        base = f"{self._scan_count} card{'s' if self._scan_count != 1 else ''} — ${self._total_value:.2f}"
        if self._unpriced_count:
            base += f" + {self._unpriced_count} unpriced"
        self._status_var.set(base)

    def _fetch_missing_prices(self) -> None:
        unpriced = self._log.get_unpriced_rows()
        count = 0
        for scan_id, tree_index in unpriced:
            row = self._logger.get_scan(scan_id)
            if row is None or not row["card_id"]:
                continue  # skip unknown rows — no card_id to fetch against
            self._log.update_price_loading(tree_index)
            future = self._executor.submit(
                _fetch_tcg_with_retry, self._price_client, row["card_id"]
            )
            self._manual_price_futures.append((future, scan_id, tree_index, "tcg", True))
            count += 1
        if count:
            self._bulk_pending += count
            self._bulk_var.set(f"Fetching prices: {self._bulk_pending} remaining")
            self._bulk_label.pack(side="left", padx=(10, 0))

    def _on_get_price_tcg(self, scan_id: int, tree_index: int) -> None:
        row = self._logger.get_scan(scan_id)
        if row is None:
            return
        self._log.update_price_loading(tree_index)
        future = self._executor.submit(self._price_client.fetch_price, row["card_id"])
        self._manual_price_futures.append((future, scan_id, tree_index, "tcg", False))

    def _on_get_price_pc(self, scan_id: int, tree_index: int) -> None:
        row = self._logger.get_scan(scan_id)
        if row is None:
            return
        self._log.update_price_loading(tree_index)
        future = self._executor.submit(
            self._pc_client.fetch_price,
            row["card_name"], row["set_name"], row["number"],
        )
        self._manual_price_futures.append((future, scan_id, tree_index, "pc", False))

    def _drain_manual_prices(self) -> None:
        still_pending = []
        for entry in self._manual_price_futures:
            future, scan_id, tree_index, source = entry[0], entry[1], entry[2], entry[3]
            is_bulk = entry[4] if len(entry) > 4 else False
            if not future.done():
                still_pending.append(entry)
                continue
            try:
                result = future.result()
            except Exception:
                result = None
            if source == "tcg":
                market_price = result.market_price if (result and result.available) else None
            else:  # "pc"
                market_price = result.loose_price if (result and result.available) else None
            self._logger.update_price(scan_id, market_price, source)
            self._log.update_price(tree_index, market_price, source)
            if market_price is not None:
                self._total_value += market_price
                self._unpriced_count = max(0, self._unpriced_count - 1)
            if is_bulk:
                self._bulk_pending -= 1
                if self._bulk_pending > 0:
                    self._bulk_var.set(f"Fetching prices: {self._bulk_pending} remaining")
                else:
                    self._bulk_label.pack_forget()
            self._update_status()
        self._manual_price_futures = still_pending

    def _on_snip_hotkey(self, _event=None) -> None:
        if self._scanning or self._state_machine is None:
            return
        self._snip_var.set("Snipping…")
        self._snip_label.config(fg="#ffcc00")
        SnipOverlay(
            self._root,
            on_capture=self._on_snip_capture,
            on_cancel=self._on_snip_cancel,
        )

    def _on_snip_cancel(self) -> None:
        shortcut_display = config.SNIP_HOTKEY.strip("<>").replace("-", "+")
        self._snip_var.set(f"Snip: {shortcut_display}")
        self._snip_label.config(fg="#888888")

    def _on_snip_capture(self, bbox: tuple) -> None:
        shortcut_display = config.SNIP_HOTKEY.strip("<>").replace("-", "+")
        self._snip_var.set(f"Snip: {shortcut_display}")
        self._snip_label.config(fg="#888888")
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            img = img.convert("RGB")
        except Exception as e:
            self._debug.log(f"Screen capture failed: {e}", "error")
            return
        scan_token = str(uuid.uuid4())
        os.makedirs(config.CAPTURES_DIR, exist_ok=True)
        try:
            img.save(os.path.join(config.CAPTURES_DIR, f"{scan_token}.jpg"))
        except Exception:
            pass
        future = self._executor.submit(
            self._run_snip_match, img, self._state_machine.matcher
        )
        self._snip_futures.append((future, scan_token))

    @staticmethod
    def _run_snip_match(img, matcher):
        from core.hasher import compute_phash
        query_hash = compute_phash(img)
        hash_180 = compute_phash(img.rotate(180))
        return matcher.find_matches(query_hash, hash_180)

    def _drain_snip_futures(self) -> None:
        still_pending = []
        for future, scan_token in self._snip_futures:
            if not future.done():
                still_pending.append((future, scan_token))
                continue
            try:
                result = future.result()
            except Exception:
                result = None
            self._handle_snip_result(result, scan_token)
        self._snip_futures = still_pending

    def _handle_snip_result(self, match_result, scan_token: str) -> None:
        from core import audio
        if match_result is None:
            closest = self._state_machine.matcher.last_closest_dist
            self._debug.log(
                f"Snip: no match (closest dist={closest}) — logged as Unknown", "error"
            )
            audio.play_failure()
            result = ScanResult(
                scan_token=scan_token,
                session_id=self._session_id,
                card_id="", card_name="Unknown",
                set_name="", number="", rarity=None,
                market_price=None, low_price=None, high_price=None,
                hamming_dist=closest if closest is not None else -1,
                price_error=None, candidates=[],
            )
        else:
            primary = match_result.primary
            self._debug.log(
                f"Snip matched: {primary.name} [{primary.set_name} #{primary.number}]"
                f" dist={primary.hamming_dist}", "match"
            )
            audio.play_card_scanned()
            candidates = [
                {"card_id": c.card_id, "card_name": c.name, "set_name": c.set_name,
                 "number": c.number, "rarity": c.rarity, "hamming_dist": c.hamming_dist}
                for c in match_result.candidates
            ]
            result = ScanResult(
                scan_token=scan_token,
                session_id=self._session_id,
                card_id=primary.card_id,
                card_name=primary.name,
                set_name=primary.set_name,
                number=primary.number,
                rarity=primary.rarity,
                market_price=None, low_price=None, high_price=None,
                hamming_dist=primary.hamming_dist,
                price_error=None, candidates=candidates,
            )
        self._handle_result(result)

    def _on_remap(self, scan_id: int, scan_token: str | None, tree_index: int) -> None:
        if self._state_machine is None:
            return
        def on_resolved(sid, card_id, card_name, set_name, number, rarity):
            self._logger.resolve(sid, card_id, card_name, set_name, number, rarity, None)
            self._log.update_resolved(tree_index, card_name, set_name, number, rarity, None)
            # Recalculate totals from DB
            rows = self._logger.get_session_scans(self._session_id)
            self._total_value = 0.0
            self._unpriced_count = 0
            for r in rows:
                p = r["market_price"]
                if p is not None:
                    self._total_value += p
                else:
                    self._unpriced_count += 1
            self._update_status()
            self._on_get_price_tcg(sid, tree_index)
            if config.PRICECHARTING_ENABLED:
                self._on_get_price_pc(sid, tree_index)

        RemapDialog(
            self._root,
            scan_id=scan_id,
            scan_token=scan_token,
            matcher=self._state_machine.matcher,
            on_resolved=on_resolved,
            remap_n=config.REMAP_TOP_N,
        )

    def _on_zoom_change(self, zoom: float) -> None:
        self._current_zoom = zoom
        self._camera.digital_zoom = zoom

    def _on_roi_change(self, roi: ROI | None) -> None:
        if self._state_machine:
            self._state_machine.roi = roi
        save_roi(roi)

    def _open_resolution(self, scan_id: int, tree_index: int) -> None:
        def on_resolved(card_name, set_name, number, rarity, market_price):
            rows = self._logger.get_session_scans(self._session_id)
            self._total_value = 0.0
            self._unpriced_count = 0
            for row in rows:
                p = row["market_price"]
                if p is not None:
                    self._total_value += p
                else:
                    self._unpriced_count += 1
            self._log.update_resolved(tree_index, card_name, set_name, number, rarity, market_price)
            self._update_status()

        ResolutionDialog(
            self._root,
            scan_id=scan_id,
            logger=self._logger,
            price_client=self._price_client,
            on_resolved=on_resolved,
        )

    def _save_session(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save session",
        )
        if not path:
            return
        try:
            self._logger.export_json(self._session_id, path)
            messagebox.showinfo("Session saved", f"Saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _load_session(self) -> None:
        if self._scan_count > 0:
            if not messagebox.askyesno(
                "Load session",
                "This will replace the current session view. Continue?",
            ):
                return
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Load session",
        )
        if not path:
            return
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "scans" not in data:
                raise ValueError("Invalid session file format.")
            rows = data["scans"]
            self._log.load_session(rows)
            self._scan_count = len(rows)
            self._total_value = sum(r["market_price"] for r in rows
                                    if r.get("market_price") is not None)
            self._unpriced_count = sum(1 for r in rows
                                       if r.get("market_price") is None)
            self._update_status()
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export scan log",
        )
        if path:
            self._logger.export_csv(path)
            messagebox.showinfo("Export complete", f"Saved to:\n{path}")

    def _on_close(self) -> None:
        save_setting("main_geometry", self._root.geometry())
        save_setting("zoom", self._current_zoom)
        save_setting("max_dist", config.MATCH_HAMMING_THRESHOLD)
        self._camera.stop()
        if self._state_machine:
            self._state_machine.shutdown()
        self._executor.shutdown(wait=False)
        self._logger.close()
        self._debug.close()
        self._root.destroy()

    def run(self) -> None:
        self._root.mainloop()
