import cv2
import os
import time
from dataclasses import dataclass, field
from utils.window_capture import WindowCapture
from utils.popup_detector import PopupDetector
import pydirectinput
import win32gui

@dataclass
class ImageTriggerConfig:
    template_path: str = os.path.join("templates", "indicator.png")
    confidence: float = 0.7
    click_interval_ms: int = 50
    running: bool = field(default=False, init=False)

class ImageTrigger:
    def __init__(self, cfg: ImageTriggerConfig):
        self.cfg = cfg

    def stop(self):
        self.cfg.running = False

    def _run(self):
        self.cfg.running = True
        self.hwnd = win32gui.FindWindow(None, "Roblox")

        while self.cfg.running:
            screenshot = WindowCapture.grab(self.hwnd)
            gray_screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

            if WindowCapture.detect_template(gray_screenshot):
                if PopupDetector.get_reference() is None:
                    PopupDetector.store_reference_region(gray_screenshot)
                
                pydirectinput.click()
                
                if PopupDetector.has_changed(gray_screenshot):
                    self.cfg.running = False
            else:
                PopupDetector.reset()

            time.sleep(self.cfg.click_interval_ms/1000)

    def update_config(self, cfg: ImageTriggerConfig):
        self.cfg = cfg
