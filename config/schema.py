from dataclasses import dataclass, field
from core.image_trigger import ImageTriggerConfig
from core.timer_keybind import TimerKeybindConfig

@dataclass
class Config:
    panic_key: str = "F8"
    visual_opacity: float = 1.0
    image_trigger_cfg: ImageTriggerConfig = field(default_factory=ImageTriggerConfig)
    timer_keybind_cfg: list[TimerKeybindConfig] = field(default_factory=list)