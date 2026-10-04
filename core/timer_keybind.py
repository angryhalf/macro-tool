import threading
import keyboard
import pydirectinput
from dataclasses import dataclass, field

@dataclass
class TimerKeybindConfig:
    key: int = 1
    interval_s: float = 1.0
    running: bool = field(default=False, init=False)

class TimerKeybindEngine:
    def __init__(self, cfg: TimerKeybindConfig):
        self.cfg = cfg
        self._thread = None
        self._stop_event = threading.Event()
        self._on_fire = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.cfg.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.cfg.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join()

    def is_running(self):
        return self.cfg.running

    def _run(self):
        while not self._stop_event.is_set():
            key_str = str(self.cfg.key)
            keyboard.press_and_release(key_str)
            pydirectinput.click()
            if callable(self._on_fire):
                self._on_fire()