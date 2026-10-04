import numpy as np

class PopupDetector:
    def __init__(self):
        self._reference = None
        self.COMPLETION_MSE_THRESHOLD = 100
        self.MONITOR_REGION_SIZE = 100

    def reset(self):
        self._reference = None

    def get_reference(self):
        return self._reference

    def store_reference_region(self, screenshot):
        h, w = screenshot.shape[::-1]
        x = max(0, w - self.MONITOR_REGION_SIZE)
        y = max(0, h - self.MONITOR_REGION_SIZE)
        self.reference = screenshot[y:h, x:w]

    def has_changed(self, screenshot):
        if self._reference is None:
            return False

        h, w = screenshot.shape[::-1]
        x = max(0, w - self.MONITOR_REGION_SIZE)
        y = max(0, h - self.MONITOR_REGION_SIZE)
        current = screenshot[y:h, x:w]

        mse = float(np.mean((self._reference - current) ** 2))
        return mse > self.COMPLETION_MSE_THRESHOLD