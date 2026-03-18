import os
import tkinter as tk
from tkinter import filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor
import uuid

import config
from core.camera import CameraCapture
from core.state_machine import ScanStateMachine, ScanResult, PriceUpdate
from core.scan_log import ScanLogger, ScanRecord
from core.price_client import PriceClient, PriceResult
from core.roi import ROI, load_roi, save_roi, load_setting, save_setting
from ui.feed_panel import FeedPanel
from ui.result_panel import ResultPanel
from ui.log_panel import LogPanel
from ui.debug_log import DebugLog
from ui.resolution_dialog import ResolutionDialog
from ui.remap_dialog import RemapDialog


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

        self._camera = CameraCapture()
        self._logger = ScanLogger()
        self._price_client = PriceClient()
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._state_machine: ScanStateMachine | None = None
        # Manual price fetches: [(future, scan_id, tree_index)]
        self._manual_price_futures: list = []

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
            on_get_price=self._on_get_price,
            on_remap=self._on_remap,
            bg="white",
        )
        self._log.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        self._debug = DebugLog(self._root)
        self._debug.pack(fill="x", padx=10, pady=(0, 5))

        status_bar = tk.Frame(self._root, bg="#1e1e1e")
        status_bar.pack(fill="x", padx=10, pady=(0, 10))

        self._status_var = tk.StringVar(value="0 cards — $0.00")
        tk.Label(status_bar, textvariable=self._status_var, fg="#aaaaaa",
                 bg="#1e1e1e", font=("Helvetica", 10)).pack(side="left", padx=10, pady=5)

        tk.Button(status_bar, text="Export CSV", command=self._export_csv,
                  font=("Helvetica", 9)).pack(side="right", padx=10, pady=4)

        self._scanning = False
        self._scan_btn = tk.Button(
            status_bar, text="▶  Enable Scanning", width=18,
            font=("Helvetica", 9, "bold"),
            bg="#333333", fg="#aaaaaa",
            activebackground="#444444",
            command=self._toggle_scanning,
        )
        self._scan_btn.pack(side="right", padx=(0, 6), pady=4)

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

        self._state_machine = ScanStateMachine(self._session_id, on_status=self._debug.log)

        # Restore saved ROI
        roi = load_roi()
        if roi:
            self._state_machine.roi = roi
            self._feed.set_roi_display(roi)
            self._debug.log("Scan area restored from settings", "dim")

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
            self._debug.log("Scanning enabled", "success")
        else:
            self._scan_btn.config(text="▶  Enable Scanning", bg="#333333", fg="#aaaaaa")
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

        self._pending_price_map[result.scan_token] = (scan_id, tree_index)

        self._scan_count += 1
        self._unpriced_count += 1
        self._update_status()

    def _handle_price_update(self, update: PriceUpdate) -> None:
        entry = self._pending_price_map.pop(update.scan_token, None)
        if entry is None:
            return
        scan_id, tree_index = entry

        self._logger.update_price(scan_id, update.market_price)
        self._log.update_price(tree_index, update.market_price)

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

    def _on_get_price(self, scan_id: int, tree_index: int) -> None:
        row = self._logger.get_scan(scan_id)
        if row is None:
            return
        self._log.update_price_loading(tree_index)
        future = self._executor.submit(self._price_client.fetch_price, row["card_id"])
        self._manual_price_futures.append((future, scan_id, tree_index))

    def _drain_manual_prices(self) -> None:
        still_pending = []
        for future, scan_id, tree_index in self._manual_price_futures:
            if not future.done():
                still_pending.append((future, scan_id, tree_index))
                continue
            try:
                price: PriceResult = future.result()
            except Exception as e:
                price = PriceResult(card_id="", market_price=None,
                                    low_price=None, high_price=None, error=str(e))
            market_price = price.market_price if price.available else None
            self._logger.update_price(scan_id, market_price)
            self._log.update_price(tree_index, market_price)
            if market_price is not None:
                self._total_value += market_price
                self._unpriced_count = max(0, self._unpriced_count - 1)
            self._update_status()
        self._manual_price_futures = still_pending

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
            # Fetch price for the remapped card
            self._on_get_price(sid, tree_index)

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
