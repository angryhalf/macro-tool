from imgui_bundle import imgui
from config.manager import ConfigManager

class SettingsPanel:
    def __init__(self, config):
        self.config = config

    def draw(self):
        _, self.config.panic_key = imgui.input_text("Panic key", self.config.panic_key, 32)

        if imgui.button("Save"):
            ConfigManager.save(self.config)
        imgui.same_line()
        if imgui.button("Load"):
            self.config = ConfigManager.load()