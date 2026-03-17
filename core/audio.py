import threading
import time
import sys
import config


def play_card_scanned() -> None:
    """Single beep — card matched, ready for next card."""
    threading.Thread(target=_beep, args=(config.BEEP_FREQUENCY_SUCCESS, config.BEEP_DURATION_MS), daemon=True).start()


def play_price_fetched() -> None:
    """Three beeps — price arrived in background."""
    threading.Thread(target=_triple_beep, args=(config.BEEP_FREQUENCY_SUCCESS, config.BEEP_DURATION_MS), daemon=True).start()


def play_failure() -> None:
    """Two short beeps — card not recognised."""
    threading.Thread(target=_double_beep, args=(config.BEEP_FREQUENCY_FAILURE, config.BEEP_DURATION_MS), daemon=True).start()


def _beep(frequency: int, duration_ms: int) -> None:
    if sys.platform == "win32":
        import winsound
        winsound.Beep(frequency, duration_ms)
    else:
        print("\a", end="", flush=True)


def _double_beep(frequency: int, duration_ms: int) -> None:
    _beep(frequency, duration_ms)
    time.sleep(0.1)
    _beep(frequency, duration_ms)


def _triple_beep(frequency: int, duration_ms: int) -> None:
    _beep(frequency, duration_ms)
    time.sleep(0.1)
    _beep(frequency, duration_ms)
    time.sleep(0.1)
    _beep(frequency, duration_ms)
