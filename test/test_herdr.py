#!/usr/bin/env python3

import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.request import urlopen


SCRIPT = Path(__file__).resolve().parents[1] / "herdr" / "cpen_session.py"
SPEC = importlib.util.spec_from_file_location("cpen_session", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HerdrPreviewTests(unittest.TestCase):
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

    def test_process_info_extracts_cpen_file(self):
        info = {
            "foreground_processes": [
                {
                    "argv": [
                        "claude",
                        "작업 대상 .pen 파일: /tmp/design/a.pen\n다음 지침",
                    ]
                }
            ]
        }
        self.assertEqual(
            MODULE.pen_file_from_process_info(info), "/private/tmp/design/a.pen"
        )

    def test_pane_title_falls_back_to_matching_cpen_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            info = root / "abc.d" / "info"
            info.parent.mkdir()
            info.write_text(
                f"{pen_file}\t123\tclaude\tpen:design\ttoken\t2026-08-04\n"
            )
            self.assertEqual(
                MODULE.pen_file_from_lease("pen:design", root),
                str(pen_file.resolve()),
            )

    def test_process_info_extracts_attached_preview_source(self):
        info = {
            "foreground_processes": [
                {
                    "argv": [
                        "python3",
                        "/tmp/cpen_session.py",
                        "preview",
                        "/tmp/a.pen",
                        "/tmp/ready",
                        "w2:p33",
                    ]
                }
            ]
        }
        self.assertEqual(
            MODULE.preview_source_from_process_info(info), "w2:p33"
        )

    def test_stop_preview_sends_q_for_graceful_cleanup(self):
        with mock.patch.object(MODULE, "run_cli") as run_cli:
            MODULE.stop_preview("w2:p33")
        run_cli.assert_called_once_with("pane", "send-text", "w2:p33", "q")

    def test_small_quadrant_uses_compact_preview(self):
        self.assertTrue(MODULE.compact_preview(28, 27))
        self.assertFalse(MODULE.compact_preview(71, 56))

    def test_clipped_text_fits_width(self):
        self.assertEqual(MODULE.clipped("abcdefgh", 5), "abcd…")

    def test_preview_access_shows_styled_link_and_copyable_url(self):
        url = "http://design-host.example.ts.net:1234/cpen/token/"
        text = MODULE.preview_access_text(url)
        link, shown_url = text.splitlines()
        self.assertIn(f"\x1b]8;;{url}\x1b\\", link)
        self.assertIn("\x1b[4;36m미리보기 열기\x1b[0m", link)
        self.assertTrue(link.endswith("\x1b]8;;\x1b\\"))
        self.assertEqual(shown_url, url)

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

    def test_tailscale_identity_uses_running_self_address(self):
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "BackendState": "Running",
                    "Self": {
                        "DNSName": "design-host.example.ts.net.",
                        "TailscaleIPs": ["100.64.0.7", "fd7a::1"],
                    },
                }
            ),
        )
        with mock.patch.object(MODULE.shutil, "which", return_value="/bin/tailscale"):
            with mock.patch.object(MODULE.subprocess, "run", return_value=completed):
                self.assertEqual(
                    MODULE.tailscale_identity(),
                    ("100.64.0.7", "design-host.example.ts.net"),
                )

    def test_browser_preview_serves_latest_png_from_memory(self):
        png = b"\x89PNG\r\n\x1a\npreview"
        with mock.patch.dict(
            MODULE.os.environ,
            {
                "CPEN_PREVIEW_BIND": "127.0.0.1",
                "CPEN_PREVIEW_HOST": "127.0.0.1",
            },
            clear=False,
        ):
            preview = MODULE.PreviewWebServer("/tmp/design.pen")
            preview.start()
        try:
            preview.update(base64.b64encode(png).decode(), "Frame 01")
            with urlopen(preview.url + "state.json", timeout=2) as response:
                state = json.loads(response.read())
            with urlopen(preview.url + "frame.png", timeout=2) as response:
                served_png = response.read()
            self.assertEqual(state["file"], "design.pen")
            self.assertEqual(state["frame"], "Frame 01")
            self.assertEqual(state["revision"], 1)
            self.assertEqual(served_png, png)
        finally:
            preview.close()

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
