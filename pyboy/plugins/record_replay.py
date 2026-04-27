#
# License: See LICENSE.md file
# GitHub: https://github.com/Baekalfen/PyBoy
#

import base64
import hashlib
import io
import json
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

import pyboy
from pyboy.plugins.base_plugin import PyBoyPlugin

logger = pyboy.logging.get_logger(__name__)


class RecordReplay(PyBoyPlugin):
    argv = [
        ("--record-input", {"action": "store_true", "help": "Record user input and save to a file (internal use)"}),
        (
            "--record-trajectory",
            {
                "action": "store_true",
                "help": "Also dump per-frame observations/interactions while playing (step_*.png/json).",
            },
        ),
        (
            "--record-trajectory-dir",
            {
                "type": str,
                "default": "human_replay",
                "help": "Root output directory for --record-trajectory dumps.",
            },
        ),
        (
            "--record-trajectory-resize",
            {
                "type": int,
                "default": 1,
                "help": "Resize factor for dumped observation frames (>=1).",
            },
        ),
    ]

    def __init__(self, *args):
        super().__init__(*args)

        if not self.enabled():
            return

        if not self.pyboy_argv.get("loadstate"):
            logger.warning(
                "To replay input consistently later, it is recommended to load a state at boot. This will be"
                "embedded into the .replay file."
            )

        logger.info("Recording event inputs")
        self.recorded_input = []
        self.current_pressed_buttons = set()
        self.trajectory_step = 0
        self.trajectory_saved_count = 0
        self.record_trajectory = bool(self.pyboy_argv.get("record_trajectory"))
        self.trajectory_resize = max(1, int(self.pyboy_argv.get("record_trajectory_resize", 1)))
        self.trajectory_output_path = None
        self.last_powerup_status = 0
        self.last_powerup_timer = 0
        self.last_superball_status = 0
        if self.record_trajectory:
            root = Path(self.pyboy_argv.get("record_trajectory_dir") or "human_replay")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.trajectory_output_path = (root / f"record_{timestamp}").resolve()
            self.trajectory_output_path.mkdir(parents=True, exist_ok=True)
            logger.info("Recording per-frame trajectory to %s", self.trajectory_output_path)

    def _update_pressed_buttons(self, events):
        for event_value in events:
            event_value = int(event_value)
            if event_value == 1:  # PRESS_ARROW_UP
                self.current_pressed_buttons.add("UP")
            elif event_value == 9:  # RELEASE_ARROW_UP
                self.current_pressed_buttons.discard("UP")
            elif event_value == 2:  # PRESS_ARROW_DOWN
                self.current_pressed_buttons.add("DOWN")
            elif event_value == 10:  # RELEASE_ARROW_DOWN
                self.current_pressed_buttons.discard("DOWN")
            elif event_value == 3:  # PRESS_ARROW_RIGHT
                self.current_pressed_buttons.add("RIGHT")
            elif event_value == 11:  # RELEASE_ARROW_RIGHT
                self.current_pressed_buttons.discard("RIGHT")
            elif event_value == 4:  # PRESS_ARROW_LEFT
                self.current_pressed_buttons.add("LEFT")
            elif event_value == 12:  # RELEASE_ARROW_LEFT
                self.current_pressed_buttons.discard("LEFT")
            elif event_value == 5:  # PRESS_BUTTON_A
                self.current_pressed_buttons.add("A")
            elif event_value == 13:  # RELEASE_BUTTON_A
                self.current_pressed_buttons.discard("A")
            elif event_value == 6:  # PRESS_BUTTON_B
                self.current_pressed_buttons.add("B")
            elif event_value == 14:  # RELEASE_BUTTON_B
                self.current_pressed_buttons.discard("B")
            elif event_value == 7:  # PRESS_BUTTON_SELECT
                self.current_pressed_buttons.add("SELECT")
            elif event_value == 15:  # RELEASE_BUTTON_SELECT
                self.current_pressed_buttons.discard("SELECT")
            elif event_value == 8:  # PRESS_BUTTON_START
                self.current_pressed_buttons.add("START")
            elif event_value == 16:  # RELEASE_BUTTON_START
                self.current_pressed_buttons.discard("START")

    def _event_name(self, event_value):
        event_names = {
            0: "QUIT",
            1: "UP",
            2: "DOWN",
            3: "RIGHT",
            4: "LEFT",
            5: "A",
            6: "B",
            7: "SELECT",
            8: "START",
            9: "UP_RELEASE",
            10: "DOWN_RELEASE",
            11: "RIGHT_RELEASE",
            12: "LEFT_RELEASE",
            13: "A_RELEASE",
            14: "B_RELEASE",
            15: "SELECT_RELEASE",
            16: "START_RELEASE",
        }
        return event_names.get(int(event_value), f"UNKNOWN_{int(event_value)}")

    def _action_string(self, events):
        action_parts = [self._event_name(e) for e in events]
        for btn in sorted(self.current_pressed_buttons):
            if not any(btn == name or btn in name for name in action_parts):
                action_parts.append(btn)
        return "_".join(action_parts) if action_parts else "NOOP"

    def _powerup_status_name(self, status):
        powerup_status_names = {
            0x00: "small",
            0x01: "growing",
            0x02: "big",
            0x03: "shrinking",
            0x04: "invincibility_blinking",
        }
        return powerup_status_names.get(int(status), f"unknown_{int(status)}")

    def _mario_state(self):
        try:
            mario = self.pyboy.game_wrapper
            world_tuple = mario.world
            powerup_status = int(self.pyboy.memory[0xFF99]) if self.pyboy else 0
            powerup_timer = int(self.pyboy.memory[0xFFA6]) if self.pyboy else 0
            superball_status = int(self.pyboy.memory[0xFFB5]) if self.pyboy else 0
            self.last_powerup_status = powerup_status
            self.last_powerup_timer = powerup_timer
            self.last_superball_status = superball_status
            return {
                "world": world_tuple[0] if world_tuple and len(world_tuple) > 0 else None,
                "level": world_tuple[1] if world_tuple and len(world_tuple) > 1 else None,
                "level_progress": float(mario.level_progress),
                "score": int(mario.score),
                "lives_left": int(mario.lives_left),
                "coins": int(mario.coins),
                "time_left": int(mario.time_left),
                "game_over": mario.game_over(),
                "death_animation": int(self.pyboy.memory[0xFFA6]) if self.pyboy else 0,
                "powerup_status": powerup_status,
                "powerup_status_name": self._powerup_status_name(powerup_status),
                "powerup_timer": powerup_timer,
                "superball_status": superball_status,
                "has_superball": bool(superball_status),
                "is_dead_or_respawning": powerup_timer > 0x80,
            }
        except Exception:
            return {
                "world": None,
                "level": None,
                "level_progress": 0.0,
                "score": 0,
                "lives_left": 0,
                "coins": 0,
                "time_left": 0,
                "game_over": False,
                "death_animation": 0,
                "powerup_status": 0,
                "powerup_status_name": "small",
                "powerup_timer": 0,
                "superball_status": 0,
                "has_superball": False,
                "is_dead_or_respawning": False,
            }

    def _save_trajectory_step(self, events):
        if not self.trajectory_output_path:
            return
        frame_idx = self.trajectory_step
        self._update_pressed_buttons(events)
        action = self._action_string(events)

        screen = self.pyboy.screen.ndarray
        img = Image.fromarray(screen[:, :, :-1] if screen.shape[2] == 4 else screen, "RGB")
        if self.trajectory_resize > 1:
            img = img.resize((img.width * self.trajectory_resize, img.height * self.trajectory_resize), Image.Resampling.LANCZOS)

        image_path = (self.trajectory_output_path / f"step_{frame_idx:05d}_observation.png").resolve()
        interaction_path = (self.trajectory_output_path / f"step_{frame_idx:05d}_interaction.json").resolve()
        state_path = self.trajectory_output_path / f"step_{frame_idx:05d}_state.state"

        record = {
            "frame": int(self.pyboy.frame_count),
            "action": action,
            "button_events": [int(e) for e in events],
            "pressed_buttons": sorted(list(self.current_pressed_buttons)),
            "image_path": str(image_path),
            **self._mario_state(),
        }

        with open(interaction_path, "w") as f:
            json.dump(record, f, indent=2)
        img.save(image_path)
        with open(state_path, "wb") as f:
            self.pyboy.save_state(f)

        self.trajectory_step += 1
        self.trajectory_saved_count += 1

    def handle_events(self, events):
        # Input recorder
        self.recorded_input.append(
            (
                self.pyboy.frame_count,
                [int(e) for e in events],
                base64.b64encode(np.ascontiguousarray(self.pyboy.screen.ndarray[:, :, :-1])).decode("utf8"),
            )
        )
        if self.record_trajectory and self.pyboy.frame_count >= 100:
            self._save_trajectory_step(events)
        return events

    def stop(self):
        save_replay(
            self.pyboy.gamerom,
            self.pyboy_argv.get("loadstate"),
            self.pyboy.gamerom + ".replay",
            self.recorded_input,
        )
        if self.record_trajectory and self.trajectory_output_path:
            logger.info(
                "Trajectory dump complete: %d frames at %s",
                self.trajectory_saved_count,
                self.trajectory_output_path,
            )

    def enabled(self):
        return self.pyboy_argv.get("record_input")


def save_replay(rom, loadstate, replay_file, recorded_input):
    with open(rom, "rb") as f:
        m = hashlib.sha256()
        m.update(f.read())
        b64_romhash = base64.b64encode(m.digest()).decode("utf8")

    if loadstate is None:
        b64_state = None
    else:
        with open(loadstate, "rb") as f:
            b64_state = base64.b64encode(f.read()).decode("utf8")

    with open(replay_file, "wb") as f:
        recorded_data = io.StringIO()
        json.dump([recorded_input, b64_romhash, b64_state], recorded_data)
        f.write(zlib.compress(recorded_data.getvalue().encode("ascii")))
