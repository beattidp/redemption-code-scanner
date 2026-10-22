import unittest
import tempfile
import os
import json
import numpy as np
import cv2
from camera_scanner.focus import FocusController

class TestFocusController(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.tmp_dir.name, "camera_config.json")
        self.fc = FocusController(config_path=self.config_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_default_config_creation(self):
        self.assertTrue(os.path.exists(self.config_path))
        with open(self.config_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data.get("focus_absolute"), 313)
        self.assertTrue(data.get("autofocus_disabled"))

    def test_save_and_reload_config(self):
        self.fc.save_config({"focus_absolute": 320, "focus_search_range": 40})
        fc2 = FocusController(config_path=self.config_path)
        self.assertEqual(fc2.config.get("focus_absolute"), 320)
        self.assertEqual(fc2.config.get("focus_search_range"), 40)

    def test_calculate_sharpness(self):
        # Blank solid gray frame should have zero sharpness
        solid = np.full((100, 100), 128, dtype=np.uint8)
        score_solid = self.fc.calculate_sharpness(solid)
        self.assertEqual(score_solid, 0.0)

        # High contrast alternating stripes should have high sharpness
        pattern = np.zeros((100, 100), dtype=np.uint8)
        pattern[:, ::4] = 255
        score_pattern = self.fc.calculate_sharpness(pattern)
        self.assertGreater(score_pattern, 100.0)

    def test_optimize_focus_with_frame_fn(self):
        # Mock frame generator that peaks sharpness at focus = 315
        def mock_frame_fn(curr_f):
            # Sharpness inversely related to distance from 315
            dist = abs(curr_f - 315)
            img = np.zeros((100, 100), dtype=np.uint8)
            step = max(2, dist + 2)
            img[:, ::step] = 255
            return img

        # Run optimize_focus with a mock device node
        current_focus = [313]
        def mock_get_frame():
            return mock_frame_fn(current_focus[0])

        original_set = self.fc.set_focus
        def fake_set(dev, val):
            current_focus[0] = val
            return True
        self.fc.set_focus = fake_set

        try:
            best_f = self.fc.optimize_focus(
                cap=None,
                device_node="/dev/videoFake",
                initial_val=313,
                search_range=20,
                step=2,
                get_frame_fn=mock_get_frame
            )
            # The search should identify the peak near 315
            self.assertIn(best_f, [313, 315, 317])
            self.assertEqual(self.fc.config.get("focus_absolute"), best_f)
        finally:
            self.fc.set_focus = original_set

if __name__ == '__main__':
    unittest.main()
