import unittest
import os
import tempfile
import cv2
import time
from camera_scanner.gui import ScannerApp

SAMPLE_IMAGE = "/home/beattidp/.gemini/antigravity/brain/385977c5-e158-4458-b428-6ce3f562f1e7/.user_uploaded/media_1791349638839.jpg"

class TestScannerGUI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_file = os.path.join(self.tmp_dir.name, "codes.txt")
        # Prepopulate with 1 code to test loading on startup
        with open(self.tmp_file, "w") as f:
            f.write("EXIST-1111-2222\n")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_gui_initialization_and_events(self):
        # Prevent opening actual camera hardware during automated test
        app = ScannerApp(default_file=self.tmp_file)
        app.update()

        # Verify initial code loaded
        self.assertEqual(app.storage.count(), 1)
        items = app.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(app.tree.item(items[0])['values'][1], "EXIST-1111-2222")

        # Simulate new code event
        app._handle_new_code("GST8S-JLPZ-KKXK")
        app.update()

        self.assertEqual(app.storage.count(), 2)
        self.assertEqual(len(app.tree.get_children()), 2)
        self.assertEqual(app.session_new_count, 1)

        # Simulate duplicate code event
        app._handle_duplicate_code("GST8S-JLPZ-KKXK")
        app.update()

        self.assertEqual(app.duplicate_count, 1)
        self.assertIn("DUPLICATE", app.banner_title_var.get())

        # Cleanup
        app._on_close()

    def test_test_mode_logging_and_exit(self):
        app = ScannerApp(default_file=self.tmp_file, test_mode=True)
        app.update()

        self.assertTrue(app.test_mode)
        self.assertIsNotNone(app.test_session_dir)
        self.assertTrue(os.path.exists(app.test_session_dir))

        dummy_frame = (cv2.imread(SAMPLE_IMAGE) if os.path.exists(SAMPLE_IMAGE)
                       else 128 * np.ones((100, 100, 3), dtype=np.uint8))

        # Handle a new code with dummy frame
        app._handle_new_code("5D4M7-K9XH-95P6", dummy_frame)
        app.update()

        # Check that test log file exists and contains entry
        self.assertTrue(os.path.exists(app.test_log_file))
        with open(app.test_log_file, "r") as f:
            content = f.read()
        self.assertIn("5D4M7-K9XH-95P6", content)

        # Close app and verify captured_codes_test.txt is generated
        app._on_close()
        test_txt_path = os.path.join(os.getcwd(), "captured_codes_test.txt")
        self.assertTrue(os.path.exists(test_txt_path))
        with open(test_txt_path, "r") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        self.assertIn("5D4M7-K9XH-95P6", lines)
        self.assertIn("EXIST-1111-2222", lines)

    def test_focus_nudging(self):
        app = ScannerApp(default_file=self.tmp_file)
        app.update()

        init_f = app.focus_controller.config.get("focus_absolute", 313)
        app._nudge_focus(3)
        self.assertEqual(app.focus_controller.config.get("focus_absolute"), init_f + 3)
        self.assertEqual(app.focus_val_lbl.cget("text"), str(init_f + 3))

        app._on_close()

if __name__ == '__main__':
    unittest.main()
