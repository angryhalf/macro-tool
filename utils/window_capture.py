import cv2
import numpy as np
import win32gui, win32ui, win32con

class WindowCapture:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.threshold = 0.7

    def grab(self):
        rect = win32gui.GetWindowRect(self.hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]

        hwnd_dc  = win32gui.GetWindowDC(self.hwnd)
        mfc_dc   = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc  = mfc_dc.CreateCompatibleDC()

        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)

        buf = bmp.GetBitmapBits(True)
        img = np.frombuffer(buf, dtype="uint8")
        img.shape = (h, w, 4)

        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC(); mfc_dc.DeleteDC()
        win32gui.ReleaseDC(self.hwnd, hwnd_dc)

        return img[..., :3]

    def detect_template(self, template):
        screen = self.grab()

        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(screen_gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        return max_val >= self.threshold