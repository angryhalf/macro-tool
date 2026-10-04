from imgui_bundle import imgui, immapp
from gui.timer_keybind_panel import TimerKeybindPanel
from gui.settings_panel import SettingsPanel
from config.manager import ConfigManager

config = ConfigManager.load()

timer_panel = TimerKeybindPanel(config.timer_keybind_cfg)
settings_panel = SettingsPanel(config)

def gui() -> None:
    # -------------------------------------------------
    # 2. Timer-Keybind window
    # -------------------------------------------------
    imgui.set_next_window_size((350, 300), imgui.Cond_.first_use_ever)
    imgui.set_next_window_pos((390, 20), imgui.Cond_.first_use_ever)
    imgui.begin("Timer Keybind")
    timer_panel.draw()
    imgui.end()

    # -------------------------------------------------
    # 3. Settings window
    # -------------------------------------------------
    imgui.set_next_window_size((350, 180), imgui.Cond_.first_use_ever)
    imgui.set_next_window_pos((20, 260), imgui.Cond_.first_use_ever)
    imgui.begin("Settings")
    settings_panel.draw()
    imgui.end()

if __name__ == "__main__":
    immapp.run(
        gui_function=gui,
        window_title="Macro",
        window_size=(800, 700),
    )