import json
import os
from .schema import Config
from core.image_trigger import ImageTriggerConfig
from core.timer_keybind import TimerKeybindConfig

_FILE = "settings.json"

class ConfigManager:
    @staticmethod
    def load() -> Config:
        if not os.path.isfile(_FILE):
            return Config()
        
        with open(_FILE) as f:
            raw = json.load(f)
        cfg = Config()
        cfg.panic_key = raw.get("panic_key", cfg.panic_key)
        cfg.visual_opacity = float(raw.get("visual_opacity", cfg.visual_opacity))
        it = raw.get("image_trigger", {})
        cfg.image_trigger_cfg = ImageTriggerConfig(
            confidence=float(it.get("confidence", 0.8)),
            click_interval_ms=int(it.get("click_interval_ms", 100))
        )
        for tk in raw.get("timer_keybinds", []):
            cfg.timer_keybind_cfg.append(TimerKeybindConfig(
                key=int(tk["key"]),
                interval_s=float(tk["interval_s"])
            ))
        return cfg

    @staticmethod
    def save(cfg: Config):
        with open(_FILE, "w") as f:
            json.dump({
                "panic_key": cfg.panic_key,
                "visual_opacity": cfg.visual_opacity,
                "image_trigger": {
                    "confidence": cfg.image_trigger_cfg.confidence,
                    "click_interval_ms": cfg.image_trigger_cfg.click_interval_ms
                },
                "timer_keybinds": [
                    {"key": c.key, "interval_s": c.interval_s}
                    for c in cfg.timer_keybind_cfg
                ]
            }, f, indent=2)