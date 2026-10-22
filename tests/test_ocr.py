import unittest
import os
import cv2
import time
import numpy as np
from camera_scanner.ocr_engine import OCREngine

SAMPLE_IMAGE = "/home/beattidp/.gemini/antigravity/brain/385977c5-e158-4458-b428-6ce3f562f1e7/stalled_package_frame.jpg"
LIVE_FRAME = "/home/beattidp/.gemini/antigravity/brain/385977c5-e158-4458-b428-6ce3f562f1e7/crop2_test.jpg"
LEADING_5_FRAME = "/tmp/pip_crop.png"
STALLED_FRAME = SAMPLE_IMAGE

class TestOCREngine(unittest.TestCase):
    def setUp(self):
        self.engine = OCREngine(required_confirmations=2, cooldown_seconds=0.1)
        self.engine.roi.y = 0.20
        self.engine.roi.h = 0.35

    def test_stalled_package_ocr(self):
        if not os.path.exists(STALLED_FRAME):
            self.skipTest("Stalled package frame not found")

        img = cv2.imread(STALLED_FRAME)
        codes = self.engine.scan_image(img)
        self.assertIn("PTX57-DF5P-9MJD", codes)

    def test_live_package_ocr(self):
        if not os.path.exists(LIVE_FRAME):
            self.skipTest("Live frame not found")

        img = cv2.imread(LIVE_FRAME)
        self.engine.use_roi = False
        codes = self.engine.scan_image(img)
        self.assertIn("JXVTS-DF9K-GSFV", codes)

    def test_leading_five_preservation(self):
        if not os.path.exists(LEADING_5_FRAME):
            self.skipTest("Leading 5 sample frame not found")

        img = cv2.imread(LEADING_5_FRAME)
        self.engine.use_roi = False
        codes = self.engine.scan_image(img)
        self.assertIn("5D4M7-K9XH-95P6", codes)

    def test_rotated_frame_simulation(self):
        if not os.path.exists(STALLED_FRAME):
            self.skipTest("Stalled package frame not found")

        img = cv2.imread(STALLED_FRAME)
        # Simulate physical fixture rotation (90 CW) and software correction (90 CCW)
        sim_camera = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        corrected = cv2.rotate(sim_camera, cv2.ROTATE_90_COUNTERCLOCKWISE)
        codes = self.engine.scan_image(corrected)
        self.assertIn("PTX57-DF5P-9MJD", codes)

    def test_jitter_guard_and_stability(self):
        if not os.path.exists(SAMPLE_IMAGE):
            self.skipTest("Sample image not found")

        img = cv2.imread(SAMPLE_IMAGE)
        blank = np.zeros_like(img)

        new_codes = []
        dup_codes = []
        known = set()

        def on_new(c):
            new_codes.append(c)
            known.add(c)

        def on_dup(c):
            dup_codes.append(c)

        def is_known(c):
            return c in known

        # Frame 1: Candidate observed (hit 1, not confirmed yet)
        self.engine.process_frame(img, on_new_code=on_new, on_duplicate=on_dup, is_known_callback=is_known)
        self.assertEqual(len(new_codes), 0)

        # Frame 2: Candidate observed again (hit 2 -> confirmed!)
        self.engine.process_frame(img, on_new_code=on_new, on_duplicate=on_dup, is_known_callback=is_known)
        self.assertEqual(len(new_codes), 1)
        self.assertEqual(new_codes[0], "PTX57-DF5P-9MJD")

        # Frame 3: Package held in place -> no repeated callback
        self.engine.process_frame(img, on_new_code=on_new, on_duplicate=on_dup, is_known_callback=is_known)
        self.assertEqual(len(new_codes), 1)
        self.assertEqual(len(dup_codes), 0)

        # Remove package: requires elapsed cooldown AND at least 4 consecutive empty frames
        time.sleep(0.15)
        for _ in range(4):
            self.engine.process_frame(blank, on_new_code=on_new, on_duplicate=on_dup, is_known_callback=is_known)

        # Re-present same package: Frame 1 (hit 1), Frame 2 (hit 2 -> confirmed duplicate!)
        self.engine.process_frame(img, on_new_code=on_new, on_duplicate=on_dup, is_known_callback=is_known)
        self.assertEqual(len(dup_codes), 0)

        self.engine.process_frame(img, on_new_code=on_new, on_duplicate=on_dup, is_known_callback=is_known)
        self.assertEqual(len(dup_codes), 1)
        self.assertEqual(dup_codes[0], "PTX57-DF5P-9MJD")

        # Frame 6: Duplicate held in place -> no repeated duplicate alert
        for _ in range(3):
            self.engine.process_frame(img, on_new_code=on_new, on_duplicate=on_dup, is_known_callback=is_known)
        self.assertEqual(len(dup_codes), 1)

if __name__ == '__main__':
    unittest.main()
