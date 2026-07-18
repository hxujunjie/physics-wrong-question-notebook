import unittest
from pathlib import Path
from unittest.mock import patch

from src import desktop_shortcut


class DesktopShortcutTests(unittest.TestCase):
    def test_resolve_prefers_vbs_in_source_tree(self):
        root = Path(__file__).resolve().parents[1]
        target, args, workdir = desktop_shortcut.resolve_launch_target(root)
        self.assertEqual(target.name, "一键启动教师端.vbs")
        self.assertEqual(args, "")
        self.assertEqual(workdir, root)

    def test_resolve_frozen_uses_exe(self):
        fake_exe = Path("C:/Apps/teacher/physics-wrong-book-teacher.exe")
        with patch.object(desktop_shortcut.sys, "frozen", True, create=True), patch.object(
            desktop_shortcut.sys, "executable", str(fake_exe)
        ):
            target, args, workdir = desktop_shortcut.resolve_launch_target()
        self.assertEqual(target, fake_exe)
        self.assertEqual(args, "")
        self.assertEqual(workdir, fake_exe.parent)

    def test_shortcut_path_is_desktop_lnk(self):
        path = desktop_shortcut.shortcut_path()
        self.assertTrue(path.name.endswith(".lnk"))
        self.assertIn("物理错题整理", path.name)


if __name__ == "__main__":
    unittest.main()
