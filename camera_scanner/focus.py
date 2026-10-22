"""UVC Camera manual focus control, configuration persistence, and sharpness optimization."""

import os
import json
import time
import logging
import subprocess
from typing import Optional, Tuple, Dict, Any, Callable
import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "camera_config.json"
DEFAULT_STARTING_FOCUS = 313


class FocusController:
    def __init__(self, config_path: str = DEFAULT_CONFIG_FILE):
        self.config_path = os.path.abspath(config_path)
        self.config: Dict[str, Any] = {}
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """Load focus configuration from JSON file or create with defaults."""
        defaults = {
            "autofocus_disabled": True,
            "focus_absolute": DEFAULT_STARTING_FOCUS,
            "focus_search_range": 30,
            "focus_step": 3
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    defaults.update(data)
            except Exception as e:
                logger.warning(f"Could not read config file {self.config_path}: {e}")
        else:
            self.config = dict(defaults)
            self.save_config(defaults)
        return defaults

    def save_config(self, new_config: Optional[Dict[str, Any]] = None) -> None:
        """Persist focus configuration to JSON file."""
        if not hasattr(self, "config") or self.config is None:
            self.config = {}
        if new_config:
            self.config.update(new_config)
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save focus config {self.config_path}: {e}")

    def disable_autofocus(self, device_node: str) -> bool:
        """Disable hardware continuous autofocus (AF = 0)."""
        if not device_node or not os.path.exists(device_node):
            return False
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", device_node, "-c", "focus_automatic_continuous=0"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0:
                self.config["autofocus_disabled"] = True
                self.save_config()
                return True
            logger.warning(f"v4l2-ctl disable AF error: {res.stderr}")
        except Exception as e:
            logger.warning(f"Error disabling autofocus on {device_node}: {e}")
        return False

    def enable_autofocus(self, device_node: str) -> bool:
        """Enable hardware continuous autofocus (AF = 1)."""
        if not device_node or not os.path.exists(device_node):
            return False
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", device_node, "-c", "focus_automatic_continuous=1"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0:
                self.config["autofocus_disabled"] = False
                self.save_config()
                return True
        except Exception as e:
            logger.warning(f"Error enabling autofocus on {device_node}: {e}")
        return False

    def set_focus(self, device_node: str, value: int) -> bool:
        """Set integer focus position (0-1023) for lens actuator."""
        if not device_node or not os.path.exists(device_node):
            return False
        val = max(0, min(1023, int(value)))
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", device_node, "-c", f"focus_absolute={val}"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0:
                self.config["focus_absolute"] = val
                self.save_config()
                return True
            logger.warning(f"v4l2-ctl set focus error: {res.stderr}")
        except Exception as e:
            logger.warning(f"Error setting focus on {device_node}: {e}")
        return False

    def get_focus(self, device_node: str) -> Optional[int]:
        """Query current focus value from hardware."""
        if not device_node or not os.path.exists(device_node):
            return self.config.get("focus_absolute", DEFAULT_STARTING_FOCUS)
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", device_node, "--get-ctrl=focus_absolute"],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            if res.returncode == 0 and "focus_absolute:" in res.stdout:
                val = int(res.stdout.split(":")[1].strip())
                return val
        except Exception as e:
            logger.warning(f"Error getting focus on {device_node}: {e}")
        return self.config.get("focus_absolute", DEFAULT_STARTING_FOCUS)

    def calculate_sharpness(self, frame: np.ndarray, roi: Optional[Tuple[int, int, int, int]] = None) -> float:
        """
        Calculate image sharpness using Laplacian variance.
        Higher score corresponds to sharper, more focused text edges.
        """
        if frame is None or frame.size == 0:
            return 0.0

        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        if roi:
            rx, ry, rw, rh = roi
            h, w = gray.shape[:2]
            x1 = max(0, min(rx, w - 1))
            y1 = max(0, min(ry, h - 1))
            x2 = max(x1 + 10, min(rx + rw, w))
            y2 = max(y1 + 10, min(ry + rh, h))
            target = gray[y1:y2, x1:x2]
        else:
            target = gray

        if target.size == 0:
            return 0.0

        return float(cv2.Laplacian(target, cv2.CV_64F).var())

    def apply_configured_focus(self, device_node: str) -> bool:
        """Apply stored autofocus state and starting focus to camera."""
        if self.config.get("autofocus_disabled", True):
            self.disable_autofocus(device_node)
        target_focus = self.config.get("focus_absolute", DEFAULT_STARTING_FOCUS)
        return self.set_focus(device_node, target_focus)

    def optimize_focus(
        self,
        cap: cv2.VideoCapture,
        device_node: str,
        initial_val: Optional[int] = None,
        search_range: Optional[int] = None,
        step: Optional[int] = None,
        get_frame_fn: Optional[Callable[[], Optional[np.ndarray]]] = None,
        on_progress: Optional[Callable[[int, float], None]] = None
    ) -> int:
        """
        Expanding search for best focus around the starting value.
        Disables continuous AF, evaluates sharpness at candidate steps,
        locks the lens actuator at the optimal peak, and updates config.
        """
        self.disable_autofocus(device_node)

        f_start = initial_val if initial_val is not None else self.config.get("focus_absolute", DEFAULT_STARTING_FOCUS)
        s_range = search_range if search_range is not None else self.config.get("focus_search_range", 30)
        s_step = step if step is not None else self.config.get("focus_step", 3)

        def measure_at(val: int) -> float:
            self.set_focus(device_node, val)
            time.sleep(0.08)  # Actuator settling delay
            if get_frame_fn:
                frame = get_frame_fn()
            elif cap and cap.isOpened():
                cap.read()  # Flush buffer frame
                ret, frame = cap.read()
                if not ret:
                    frame = None
            else:
                frame = None

            if frame is None:
                return 0.0

            score = self.calculate_sharpness(frame)
            if on_progress:
                on_progress(val, score)
            return score

        # Evaluate initial baseline
        best_f = f_start
        best_score = measure_at(f_start)

        # 1. Probe direction: sample f_start - step*2 and f_start + step*2
        f_low = max(0, f_start - s_step * 2)
        f_high = min(1023, f_start + s_step * 2)
        score_low = measure_at(f_low)
        score_high = measure_at(f_high)

        # Determine search direction and bound
        if score_low > best_score:
            best_f = f_low
            best_score = score_low
            direction = -1
        elif score_high > best_score:
            best_f = f_high
            best_score = score_high
            direction = 1
        else:
            direction = 0  # Peak is likely right near f_start

        # 2. Expand search in identified direction
        min_f = max(0, f_start - s_range)
        max_f = min(1023, f_start + s_range)

        if direction != 0:
            curr = best_f + direction * s_step
            while min_f <= curr <= max_f:
                score = measure_at(curr)
                if score > best_score:
                    best_score = score
                    best_f = curr
                    curr += direction * s_step
                else:
                    # Score dropped; peak passed
                    break
        else:
            # Check fine neighbors in both directions
            for offset in [-s_step, s_step]:
                curr = f_start + offset
                if min_f <= curr <= max_f:
                    score = measure_at(curr)
                    if score > best_score:
                        best_score = score
                        best_f = curr

        # Set hardware lens actuator to winning focus position
        self.set_focus(device_node, best_f)
        self.config["focus_absolute"] = best_f
        self.save_config()
        logger.info(f"Optimal focus locked at {best_f} (sharpness: {best_score:.1f})")
        return best_f
