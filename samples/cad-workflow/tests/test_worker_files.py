import os
from pathlib import Path
import tempfile
import unittest

from cad_workflow.worker import read_regular, write_new


class FrozenFileTests(unittest.TestCase):
    def test_freezing_preserves_bytes_and_refuses_to_replace_an_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidate"
            write_new(path, b"native-candidate")
            self.assertEqual(read_regular(path), b"native-candidate")
            self.assertEqual(path.stat().st_mode & 0o222, 0)
            with self.assertRaises(FileExistsError):
                write_new(path, b"replacement")
            self.assertEqual(path.read_bytes(), b"native-candidate")

    def test_symlink_hardlink_empty_file_and_fifo_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            regular = directory / "regular"
            regular.write_bytes(b"candidate")
            symbolic = directory / "symbolic"
            symbolic.symlink_to(regular)
            with self.assertRaises(OSError):
                read_regular(symbolic)
            linked = directory / "linked"
            os.link(regular, linked)
            with self.assertRaises(ValueError):
                read_regular(linked)
            empty = directory / "empty"
            empty.touch()
            with self.assertRaises(ValueError):
                read_regular(empty)
            fifo = directory / "fifo"
            os.mkfifo(fifo)
            # O_NONBLOCK ensures a pipe cannot hang the trusted supervisor.
            with self.assertRaises(ValueError):
                read_regular(fifo)
