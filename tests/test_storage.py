import unittest
import tempfile
import os
from camera_scanner.storage import CodeStorage

class TestCodeStorage(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.tmp_dir.name, "codes.txt")
        self.storage = CodeStorage(default_filepath=self.file_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_add_and_duplicate(self):
        is_new, entry, orig = self.storage.add_code("GST8S-JLPZ-KKXK")
        self.assertTrue(is_new)
        self.assertEqual(entry.code, "GST8S-JLPZ-KKXK")
        self.assertEqual(entry.index, 1)
        self.assertIsNone(orig)

        # Duplicate check
        is_new2, entry2, orig2 = self.storage.add_code("gst8s-jlpz-kkxk")
        self.assertFalse(is_new2)
        self.assertEqual(orig2.code, "GST8S-JLPZ-KKXK")
        self.assertEqual(orig2.index, 1)

    def test_persistence_and_restart(self):
        self.storage.add_code("CODE1-AAAA-1111")
        self.storage.add_code("CODE2-BBBB-2222")
        self.assertEqual(self.storage.count(), 2)

        # Create new storage instance pointing to same file (restart app)
        storage2 = CodeStorage(default_filepath=self.file_path)
        loaded = storage2.load_from_file()
        self.assertEqual(loaded, 2)
        self.assertEqual(storage2.count(), 2)
        self.assertTrue(storage2.is_duplicate("CODE1-AAAA-1111"))

        # Add 3rd code
        is_new, entry, _ = storage2.add_code("CODE3-CCCC-3333")
        self.assertTrue(is_new)
        self.assertEqual(entry.index, 3)

    def test_update_code(self):
        self.storage.add_code("AAAA1-1111-1111")
        self.storage.add_code("BBBB2-2222-2222")

        # Valid update
        ok, err = self.storage.update_code(1, "CCCC3-3333-3333")
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(self.storage.entries[0].code, "CCCC3-3333-3333")
        self.assertEqual(self.storage.entries[0].source, "edited")
        self.assertTrue(self.storage.is_duplicate("CCCC3-3333-3333"))
        self.assertFalse(self.storage.is_duplicate("AAAA1-1111-1111"))

        # Verify disk persistence after edit
        with open(self.file_path, "r") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        self.assertEqual(lines, ["CCCC3-3333-3333", "BBBB2-2222-2222"])

        # Update to existing code should fail
        ok, err = self.storage.update_code(1, "BBBB2-2222-2222")
        self.assertFalse(ok)
        self.assertIn("already exists", err)

        # Out of bounds index
        ok, err = self.storage.update_code(99, "XXXXX-XXXX-XXXX")
        self.assertFalse(ok)
        self.assertIn("out of range", err)

    def test_delete_code(self):
        self.storage.add_code("CODE1-1111-1111")
        self.storage.add_code("CODE2-2222-2222")
        self.storage.add_code("CODE3-3333-3333")

        # Delete middle entry (index 2)
        ok, deleted = self.storage.delete_code(2)
        self.assertTrue(ok)
        self.assertEqual(deleted.code, "CODE2-2222-2222")
        self.assertEqual(self.storage.count(), 2)

        # Check re-indexing
        entries = self.storage.get_all()
        self.assertEqual(entries[0].index, 1)
        self.assertEqual(entries[0].code, "CODE1-1111-1111")
        self.assertEqual(entries[1].index, 2)
        self.assertEqual(entries[1].code, "CODE3-3333-3333")

        # Check is_duplicate
        self.assertFalse(self.storage.is_duplicate("CODE2-2222-2222"))
        self.assertTrue(self.storage.is_duplicate("CODE3-3333-3333"))

        # Check disk file
        with open(self.file_path, "r") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        self.assertEqual(lines, ["CODE1-1111-1111", "CODE3-3333-3333"])

        # Out of bounds delete
        ok, deleted = self.storage.delete_code(10)
        self.assertFalse(ok)
        self.assertIsNone(deleted)

if __name__ == '__main__':
    unittest.main()
