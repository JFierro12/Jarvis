"""Tests for the packaged desktop app's log destination.

The packaged build has `console=False`, so the log file is the only way a
failure can be reported. These tests cover the case that used to make that
impossible: a log path that cannot be opened taking the whole app down during
module import, with no console to say why.
"""

import logging
from pathlib import Path

from app.core import desktop_logging


class TestLogDirectory:
    def test_windows_uses_localappdata(self):
        path = desktop_logging.log_directory(
            platform="win32", env={"LOCALAPPDATA": r"C:\Users\someone\AppData\Local"}
        )
        assert path == Path(r"C:\Users\someone\AppData\Local") / "Jarvis"

    def test_windows_without_localappdata_falls_back_under_home(self):
        path = desktop_logging.log_directory(platform="win32", env={})
        assert path == Path.home() / "AppData" / "Local" / "Jarvis"

    def test_posix_uses_xdg_state_home(self):
        path = desktop_logging.log_directory(
            platform="linux", env={"XDG_STATE_HOME": "/home/someone/.local/state"}
        )
        assert path == Path("/home/someone/.local/state") / "Jarvis"

    def test_posix_without_xdg_falls_back(self):
        path = desktop_logging.log_directory(platform="linux", env={})
        assert path == Path.home() / ".local" / "state" / "Jarvis"

    def test_the_log_is_not_placed_beside_the_interpreter(self):
        """The regression this module exists to prevent.

        The path used to be `Path(sys.executable).parent / "jarvis.log"`. Run
        from source that is the venv's Scripts directory; installed under
        Program Files it is usually unwritable, and opening it raised during
        import -- so the app failed to start and the reason went nowhere,
        because a windowed build has no console.
        """
        import sys

        path = desktop_logging.log_path()
        assert Path(sys.executable).resolve().parent != path.parent
        assert "Scripts" not in path.parts
        assert "site-packages" not in path.parts

    def test_the_filename_is_stable(self):
        assert desktop_logging.log_path(platform="win32", env={"LOCALAPPDATA": "C:/x"}).name == "jarvis.log"


class TestBuildHandlers:
    def test_a_writable_path_gives_a_file_handler(self, tmp_path):
        handlers = desktop_logging.build_handlers(tmp_path / "sub" / "jarvis.log")
        try:
            assert len(handlers) == 1
            assert isinstance(handlers[0], logging.FileHandler)
            assert (tmp_path / "sub").is_dir(), "parent directory was not created"
        finally:
            for handler in handlers:
                handler.close()

    def test_an_unopenable_path_degrades_instead_of_raising(self, tmp_path):
        """The important case.

        A file where a directory needs to be makes mkdir raise OSError. The
        app must still start: a stream handler that goes nowhere in a windowed
        build is strictly better than a crash during import that also goes
        nowhere.
        """
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")

        handlers = desktop_logging.build_handlers(blocker / "nested" / "jarvis.log")
        try:
            assert len(handlers) == 1
            assert isinstance(handlers[0], logging.StreamHandler)
            assert not isinstance(handlers[0], logging.FileHandler)
        finally:
            for handler in handlers:
                handler.close()

    def test_building_handlers_never_raises_for_a_silly_path(self, tmp_path):
        # A path far longer than any filesystem accepts.
        silly = tmp_path / ("x" * 200) / ("y" * 200) / ("z" * 200) / "jarvis.log"
        handlers = desktop_logging.build_handlers(silly)
        try:
            assert handlers, "no handler returned at all"
        finally:
            for handler in handlers:
                handler.close()


class TestDesktopMainUsesIt:
    def test_desktop_main_does_not_derive_the_log_path_from_sys_executable(self):
        """Asserted on the AST, so the comment explaining the old approach
        does not fail the test that forbids it."""
        import ast

        # Read as text rather than imported: importing desktop_main runs
        # logging.basicConfig and pulls in the whole ASGI app, which would
        # write a real log into the user's profile just to inspect a path.
        source = (Path(__file__).resolve().parents[1] / "app" / "desktop_main.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if ast.unparse(target) == "_LOG_PATH":
                        rendered = ast.unparse(node.value)
                        assert "sys.executable" not in rendered, (
                            f"_LOG_PATH is derived from the interpreter path: {rendered}"
                        )
                        assert "desktop_logging" in rendered
                        return
        raise AssertionError("_LOG_PATH assignment not found in desktop_main")
