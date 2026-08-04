#!/usr/bin/env python3

import base64
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "herdr" / "cpen_session.py"
SPEC = importlib.util.spec_from_file_location("cpen_session", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HerdrPreviewTests(unittest.TestCase):
    def test_png_size_reads_ihdr(self):
        header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 320, 640)
        encoded = base64.b64encode(header).decode()
        self.assertEqual(MODULE.png_size(encoded), (320, 640))

    def test_context_values_prefer_explicit_launcher_environment(self):
        old = dict(MODULE.os.environ)
        try:
            MODULE.os.environ["CPEN_HERDR_CWD"] = "/tmp"
            MODULE.os.environ["CPEN_HERDR_WORKSPACE"] = "w9"
            self.assertEqual(MODULE.invocation_values(), ("/private/tmp", "w9"))
        finally:
            MODULE.os.environ.clear()
            MODULE.os.environ.update(old)

    def test_popup_open_targets_the_active_pane(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(MODULE.open_launcher(), 0)
        command = run.call_args.args[0]
        self.assertNotIn("--workspace", command)
        self.assertNotIn("--target-pane", command)
        self.assertNotIn("--cwd", command)

    def test_pen_open_exit_code_is_not_authoritative(self):
        completed = mock.Mock(returncode=1)
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            MODULE.open_pen("/tmp/example.pen")
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertNotIn("-g", run.call_args.args[0])

    def test_wait_for_ready_observes_signal_file(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory, "ready")
            ready.touch()
            self.assertTrue(MODULE.wait_for_ready(ready, timeout=0))

    def test_pane_run_accepts_empty_stdout(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed):
            MODULE.run_cli("pane", "run", "w1:p2", "true")

    def test_pane_run_passes_one_shell_command(self):
        with mock.patch.object(MODULE, "run_cli") as run_cli:
            MODULE.pane_run("w1:p2", ["fish", "/tmp/run cpen.fish", "a b"])
        run_cli.assert_called_once_with(
            "pane", "run", "w1:p2", "fish '/tmp/run cpen.fish' 'a b'"
        )

    def test_agent_command_uses_script_arguments_instead_of_fish_lc(self):
        command = MODULE.agent_command("codex", "/tmp/a b.pen", "a b")
        self.assertEqual(command[0], "fish")
        self.assertEqual(command[1], str(MODULE.CPEN_RUNNER))
        self.assertNotIn("-lc", command)
        self.assertEqual(command[-4:], ["codex", "--file", "/tmp/a b.pen", "pen:a b"])

    def test_pencil_mcp_uses_unique_preview_agent_without_conversation_id(self):
        first = MODULE.pencil_mcp_command(Path("/tmp/mcp"))
        second = MODULE.pencil_mcp_command(Path("/tmp/mcp"))
        self.assertTrue(first[-1].startswith("cpenPreview-"))
        self.assertNotEqual(first[-1], second[-1])
        self.assertNotIn("--conversation_id", first)

    def test_fit_grid_preserves_aspect_ratio_without_upscaling(self):
        self.assertEqual(MODULE.fit_grid(720, 1560, 71, 56, 16, 30), (45, 52))

    def test_export_png_reads_and_immediately_deletes_file(self):
        mcp = object.__new__(MODULE.PencilMCP)
        png = b"\x89PNG\r\n\x1a\npreview"

        def export(_name, arguments):
            Path(arguments["outputDir"], "frame.png").write_bytes(png)
            return {}

        with tempfile.TemporaryDirectory() as output_dir:
            with mock.patch.object(mcp, "call", side_effect=export):
                encoded = mcp.export_png("/tmp/design.pen", "frame", output_dir)
            self.assertEqual(base64.b64decode(encoded), png)
            self.assertEqual(list(Path(output_dir).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
