import os
import tempfile
import unittest

from resource_archive import ResourceArchive
from resource_editor_gui import ResourceEditorApp


class BulkImportTests(unittest.TestCase):
    def test_folder_files_keeps_generated_from_parent(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "generated", "sound", "test.bin")
            os.makedirs(os.path.dirname(path))
            with open(path, "wb") as fh:
                fh.write(b"new")
            with open(os.path.join(root, "do-not-import.resources"), "wb") as fh:
                fh.write(b"archive")

            files = ResourceEditorApp._folder_files(root)

            self.assertEqual(files, [("generated/sound/test.bin", path)])

    def test_folder_files_keeps_generated_when_selected_directly(self):
        with tempfile.TemporaryDirectory() as root:
            generated = os.path.join(root, "generated")
            path = os.path.join(generated, "maps", "test.bin")
            os.makedirs(os.path.dirname(path))
            with open(path, "wb") as fh:
                fh.write(b"new")

            files = ResourceEditorApp._folder_files(generated)

            self.assertEqual(files, [("generated/maps/test.bin", path)])

    def test_upsert_replaces_case_insensitively_and_adds(self):
        archive = ResourceArchive()
        original = archive.add_entry("generated/old.bin", b"old")

        replaced, was_added = archive.upsert_entry(
            "GENERATED/OLD.BIN", b"replacement")
        added, new_was_added = archive.upsert_entry(
            "generated/new.bin", b"new")

        self.assertIs(replaced, original)
        self.assertFalse(was_added)
        self.assertEqual(replaced.data, b"replacement")
        self.assertEqual(replaced.length, len(b"replacement"))
        self.assertTrue(new_was_added)
        self.assertIs(archive.find("generated/new.bin"), added)


if __name__ == "__main__":
    unittest.main()
