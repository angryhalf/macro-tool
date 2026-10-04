from imgui_bundle import imgui
from core.timer_keybind import TimerKeybindConfig, TimerKeybindEngine

class TimerKeybindPanel:
    def __init__(self, cfg_list):
        self._post_cfg   = TimerKeybindConfig(key=2, interval_s=0.5)
        self._post_engine = TimerKeybindEngine(self._post_cfg)

        self.cfg_list = cfg_list
        self.engines  = [TimerKeybindEngine(c) for c in cfg_list]

        for eng in self.engines:
            eng.on_fire = self._after_any_fire

    def _after_any_fire(self):
        if not self._post_engine.is_running():
            self._post_engine.start()

    def draw(self):
        if imgui.collapsing_header("Rod keybind", True)[0]:
            _, self._post_cfg.key = imgui.combo(
                "Key##post_key",
                self._post_cfg.key - 1,
                [str(i) for i in range(1, 10)]
            )
            self._post_cfg.key += 1

            _, self._post_cfg.interval_s = imgui.input_float(
                "Interval (s)##post_interval", self._post_cfg.interval_s, 0.1
            )
            self._post_cfg.interval_s = max(0.1, self._post_cfg.interval_s)

        imgui.separator()

        if imgui.button("Add keybind##timer_add"):
            self.cfg_list.append(TimerKeybindConfig())
            new_eng = TimerKeybindEngine(self.cfg_list[-1])
            new_eng.on_fire = self._after_any_fire
            self.engines.append(new_eng)

        for idx, (cfg, engine) in enumerate(zip(self.cfg_list, self.engines)):
            imgui.push_id(f"timer_{idx}")
            if imgui.collapsing_header(f"Keybind {idx + 1}", True)[0]:
                _, cfg.key = imgui.combo(
                    "Key##key", cfg.key - 1, [str(i) for i in range(1, 10)]
                )
                cfg.key += 1

                _, cfg.interval_s = imgui.input_float(
                    "Interval (s)##interval", cfg.interval_s, 0.1
                )
                cfg.interval_s = max(0.1, cfg.interval_s)

                imgui.same_line()
                if imgui.button("Remove##timer"):
                    engine.stop()
                    del self.cfg_list[idx]
                    del self.engines[idx]
                    imgui.pop_id()
                    break
            imgui.pop_id()