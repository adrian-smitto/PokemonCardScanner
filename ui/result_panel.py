import tkinter as tk
from tkinter import ttk
from core.state_machine import ScanResult


class ResultPanel(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#1e1e1e", **kwargs)
        self._build()

    def _build(self) -> None:
        tk.Label(self, text="LAST SCAN", font=("Helvetica", 9, "bold"),
                 fg="#888888", bg="#1e1e1e").pack(anchor="w", padx=10, pady=(10, 0))

        self._name_var = tk.StringVar(value="—")
        tk.Label(self, textvariable=self._name_var, font=("Helvetica", 14, "bold"),
                 fg="white", bg="#1e1e1e", wraplength=280, justify="left").pack(
            anchor="w", padx=10, pady=(4, 0))

        self._set_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self._set_var, font=("Helvetica", 10),
                 fg="#aaaaaa", bg="#1e1e1e").pack(anchor="w", padx=10)

        self._rarity_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self._rarity_var, font=("Helvetica", 10),
                 fg="#aaaaaa", bg="#1e1e1e").pack(anchor="w", padx=10)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=8)

        self._price_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self._price_var, font=("Helvetica", 22, "bold"),
                 fg="#4caf50", bg="#1e1e1e").pack(anchor="w", padx=10)

        self._range_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self._range_var, font=("Helvetica", 9),
                 fg="#888888", bg="#1e1e1e").pack(anchor="w", padx=10)

        self._note_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self._note_var, font=("Helvetica", 9, "italic"),
                 fg="#e57373", bg="#1e1e1e", wraplength=280, justify="left").pack(
            anchor="w", padx=10, pady=(4, 0))

    def display(self, result: ScanResult) -> None:
        self._name_var.set(result.card_name)
        self._set_var.set(f"{result.set_name}  #{result.number}")
        self._rarity_var.set(result.rarity or "")

        if result.market_price is not None:
            self._price_var.set(f"${result.market_price:.2f}")
            self._price_var_color("#4caf50")
            low = f"${result.low_price:.2f}" if result.low_price is not None else "—"
            high = f"${result.high_price:.2f}" if result.high_price is not None else "—"
            self._range_var.set(f"Low {low}  ·  High {high}")
        else:
            self._price_var.set("N/A")
            self._price_var_color("#e57373")
            self._range_var.set("")

        if result.price_error:
            self._note_var.set("Price unavailable — API error")
        else:
            self._note_var.set("")

    def clear(self) -> None:
        self._name_var.set("—")
        self._set_var.set("")
        self._rarity_var.set("")
        self._price_var.set("")
        self._range_var.set("")
        self._note_var.set("")

    def show_not_recognized(self) -> None:
        self._name_var.set("Card not recognized")
        self._set_var.set("")
        self._rarity_var.set("")
        self._price_var.set("")
        self._range_var.set("")
        self._note_var.set("Remove the card and try again")

    def _price_var_color(self, color: str) -> None:
        for widget in self.winfo_children():
            if isinstance(widget, tk.Label) and widget.cget("textvariable") == str(self._price_var):
                widget.config(fg=color)
                break
