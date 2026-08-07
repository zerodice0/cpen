#!/usr/bin/env python3

import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen


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

    def test_pencil_socket_calls_exact_file(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "pencil-desktop.sock")
            server = MODULE.socket.socket(
                MODULE.socket.AF_UNIX, MODULE.socket.SOCK_STREAM
            )
            server.bind(socket_path)
            server.listen(1)
            received = []

            def receive(connection):
                data = b""
                while b"\f" not in data:
                    data += connection.recv(65536)
                return json.loads(data.split(b"\f", 1)[0])

            def serve():
                connection, _ = server.accept()
                assignment = json.dumps(
                    {
                        "type": "tool_response",
                        "data": {
                            "request_id": "client-id-assignment",
                            "success": True,
                            "client_id": "client-1",
                        },
                    }
                ).encode() + b"\f"
                connection.sendall(assignment[:11])
                connection.sendall(assignment[11:])
                received.append(receive(connection))
                request = receive(connection)
                received.append(request)
                request_id = request["data"]["request_id"]
                response = {
                    "type": "tool_response",
                    "data": {
                        "client_id": "client-1",
                        "request_id": request_id,
                        "success": True,
                        "result": {
                            "message": 'OK\n\n## Print output\n{"id":"f1","name":"Frame 1"}'
                        },
                    },
                }
                connection.sendall(json.dumps(response).encode() + b"\f")
                connection.close()

            thread = threading.Thread(target=serve)
            thread.start()
            try:
                with mock.patch.dict(
                    MODULE.os.environ, {"CPEN_PENCIL_SOCKET": socket_path}, clear=True
                ):
                    mcp = MODULE.PencilMCP()
                    frames = mcp.frames("/tmp/example.pen")
                    mcp.close()
            finally:
                thread.join(timeout=2)
                server.close()

        self.assertEqual(frames, [{"id": "f1", "name": "Frame 1"}])
        self.assertEqual(received[0]["type"], "agent_connected")
        self.assertTrue(received[0]["data"]["agent"].startswith("cpenPreview-"))
        self.assertEqual(
            received[1]["data"]["payload"]["filePath"], "/tmp/example.pen"
        )

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

    def test_pane_binding_survives_cleared_process_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            with mock.patch.dict(
                MODULE.os.environ, {"CPEN_BINDING_DIR": str(root / "bindings")}
            ):
                MODULE.write_binding("w1:p2", str(pen_file), "w1:p3")
                binding = MODULE.read_binding("w1:p2")
                self.assertEqual(binding["pen_file"], str(pen_file.resolve()))
                self.assertEqual(binding["preview_pane_id"], "w1:p3")
                self.assertEqual(MODULE.pen_file_from_process_info({}), "")

    def test_plugin_reads_binding_created_by_standalone_cpen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            with mock.patch.dict(
                MODULE.os.environ,
                {"XDG_CACHE_HOME": str(root / "cache")},
                clear=True,
            ):
                MODULE.write_binding("w1:p2", str(pen_file))
                MODULE.os.environ["HERDR_PLUGIN_STATE_DIR"] = str(root / "plugin")
                self.assertEqual(
                    MODULE.read_binding("w1:p2")["pen_file"],
                    str(pen_file.resolve()),
                )

    def test_binding_rows_show_all_other_panes_for_same_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            with mock.patch.dict(
                MODULE.os.environ, {"CPEN_BINDING_DIR": str(root / "bindings")}
            ):
                MODULE.write_binding("w1:p2", str(pen_file))
                MODULE.write_binding("w1:p4", str(pen_file))
                rows = MODULE.pen_file_rows([str(pen_file)], str(root), "w1:p2")
                self.assertIn("⚠ 연결됨: w1:p4", rows[0])
                self.assertNotIn("w1:p2", rows[0])

    def test_moving_pane_moves_source_binding_and_preview_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.pen"
            second = root / "second.pen"
            first.touch()
            second.touch()
            with mock.patch.dict(
                MODULE.os.environ, {"CPEN_BINDING_DIR": str(root / "bindings")}
            ):
                MODULE.write_binding("w1:p2", str(first), "w1:p3")
                MODULE.write_binding("w1:p8", str(second), "w1:p2")
                MODULE.move_binding("w1:p2", "w2:p5")
                self.assertIsNone(MODULE.read_binding("w1:p2"))
                self.assertEqual(
                    MODULE.read_binding("w2:p5")["pen_file"], str(first.resolve())
                )
                self.assertEqual(
                    MODULE.read_binding("w1:p8")["preview_pane_id"], "w2:p5"
                )

    def test_closed_preview_clears_source_binding_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "HERDR_PLUGIN_EVENT": "pane.closed",
                "HERDR_PLUGIN_EVENT_JSON": json.dumps({"pane_id": "w1:p3"}),
            }
            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                MODULE.write_binding("w1:p2", str(pen_file), "w1:p3")
                self.assertEqual(MODULE.handle_plugin_event(), 0)
                self.assertEqual(
                    MODULE.read_binding("w1:p2")["preview_pane_id"], ""
                )

    def test_closed_source_removes_binding_and_closes_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "HERDR_PLUGIN_EVENT": "pane.closed",
                "HERDR_PLUGIN_EVENT_JSON": json.dumps({"pane_id": "w1:p2"}),
            }
            completed = mock.Mock(returncode=0)
            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                MODULE.write_binding("w1:p2", str(pen_file), "w1:p3")
                with mock.patch.object(
                    MODULE.subprocess, "run", return_value=completed
                ) as run:
                    self.assertEqual(MODULE.handle_plugin_event(), 0)
                self.assertIsNone(MODULE.read_binding("w1:p2"))
            self.assertEqual(
                run.call_args.args[0][-3:], ["pane", "close", "w1:p3"]
            )

    def test_unbound_pane_opens_attach_popup_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "CPEN_BINDING_DIR": str(Path(directory) / "bindings"),
                "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
                    {"focused_pane_id": "w1:p2", "workspace_id": "w1"}
                ),
            }
            pane_list = {
                "result": {
                    "panes": [
                        {"pane_id": "w1:p2", "tab_id": "w1:t1", "cwd": directory}
                    ]
                }
            }
            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                with mock.patch.object(MODULE, "pane_process_info", return_value={}):
                    with mock.patch.object(MODULE, "run_json", return_value=pane_list):
                        with mock.patch.object(
                            MODULE, "open_attach_launcher", return_value=0
                        ) as launcher:
                            self.assertEqual(MODULE.toggle_preview(), 0)
            launcher.assert_called_once_with("w1:p2")

    def test_cleared_pane_uses_persisted_binding_without_picker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
                    {"focused_pane_id": "w1:p2", "workspace_id": "w1"}
                ),
            }
            pane_list = {
                "result": {
                    "panes": [
                        {"pane_id": "w1:p2", "tab_id": "w1:t1", "cwd": directory}
                    ]
                }
            }
            split = {"result": {"pane": {"pane_id": "w1:p3"}}}

            def run_json(*args):
                return pane_list if args[1] == "list" else split

            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                MODULE.write_binding("w1:p2", str(pen_file))
                with mock.patch.object(MODULE, "pane_process_info", return_value={}):
                    with mock.patch.object(MODULE, "run_json", side_effect=run_json):
                        with mock.patch.object(MODULE, "run_cli"):
                            with mock.patch.object(MODULE, "start_preview") as preview:
                                with mock.patch.object(
                                    MODULE, "open_attach_launcher"
                                ) as launcher:
                                    self.assertEqual(MODULE.toggle_preview(), 0)
                launcher.assert_not_called()
                preview.assert_called_once_with(
                    "w1:p3", str(pen_file.resolve()), "w1:p2"
                )
                self.assertEqual(
                    MODULE.read_binding("w1:p2")["preview_pane_id"], "w1:p3"
                )

    def test_restored_stale_preview_pane_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
                    {"focused_pane_id": "w1:p2", "workspace_id": "w1"}
                ),
            }
            pane_list = {
                "result": {
                    "panes": [
                        {"pane_id": "w1:p2", "tab_id": "w1:t1", "cwd": directory},
                        {"pane_id": "w1:p3", "tab_id": "w1:t1", "cwd": directory},
                    ]
                }
            }
            split = {"result": {"pane": {"pane_id": "w1:p4"}}}

            def run_json(*args):
                return pane_list if args[1] == "list" else split

            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                MODULE.write_binding("w1:p2", str(pen_file), "w1:p3")
                with mock.patch.object(MODULE, "pane_process_info", return_value={}):
                    with mock.patch.object(MODULE, "run_json", side_effect=run_json):
                        with mock.patch.object(MODULE, "run_cli"):
                            with mock.patch.object(MODULE, "start_preview") as preview:
                                with mock.patch.object(
                                    MODULE.subprocess,
                                    "run",
                                    return_value=mock.Mock(returncode=0),
                                ) as run:
                                    self.assertEqual(MODULE.toggle_preview(), 0)
                self.assertIn(
                    [MODULE.herdr_bin(), "pane", "close", "w1:p3"],
                    [call.args[0] for call in run.call_args_list],
                )
                preview.assert_called_once_with(
                    "w1:p4", str(pen_file.resolve()), "w1:p2"
                )
                self.assertEqual(
                    MODULE.read_binding("w1:p2")["preview_pane_id"], "w1:p4"
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

    def test_osc52_copy_encodes_url_for_clipboard(self):
        url = "http://design-host.example.ts.net:1234/cpen/token/"
        sequence = MODULE.osc52_copy(url)
        self.assertTrue(sequence.startswith("\x1b]52;c;"))
        self.assertTrue(sequence.endswith("\x07"))
        payload = sequence.removeprefix("\x1b]52;c;").removesuffix("\x07")
        self.assertEqual(base64.b64decode(payload).decode(), url)

    def test_browser_preview_polls_cached_images_quickly(self):
        html = MODULE.PREVIEW_HTML.read_text()
        self.assertIn("setInterval(refresh, 150)", html)

    def test_browser_preview_retries_failed_frame_revision(self):
        html = MODULE.PREVIEW_HTML.read_text()
        self.assertIn("let loadingRevision = null;", html)
        self.assertIn(
            "state.revision !== revision && loadingRevision === null", html
        )
        self.assertNotIn("revision = state.revision;", html)

        onload = html.split("image.onload = () => {", 1)[1].split("};", 1)[0]
        self.assertIn("revision = requestedRevision;", onload)
        self.assertIn("loadingRevision = null;", onload)

        onerror = html.split("image.onerror = () => {", 1)[1].split("};", 1)[0]
        self.assertIn("loadingRevision = null;", onerror)

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

    def test_agent_command_passes_external_binding_occupants(self):
        command = MODULE.agent_command(
            "codex", "/tmp/a.pen", "a", "w1:p2, w1:p4"
        )
        self.assertEqual(command[:2], ["env", "CPEN_EXTERNAL_OCCUPANTS=w1:p2, w1:p4"])
        self.assertEqual(command[2], "fish")

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
        frames = ["Frame 01", "Frame 02", "Frame 03", "Frame 04"]
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
            preview.select("Frame 02", 2, frames)
            preview.update(base64.b64encode(png).decode())
            with urlopen(preview.url + "state.json", timeout=2) as response:
                state = json.loads(response.read())
            with urlopen(preview.url + "frame.png", timeout=2) as response:
                served_png = response.read()
            self.assertEqual(state["file"], "design.pen")
            self.assertEqual(state["frame"], "Frame 02")
            self.assertEqual(state["revision"], 1)
            self.assertEqual(state["position"], 2)
            self.assertEqual(state["count"], 4)
            self.assertEqual(state["frames"], frames)
            self.assertTrue(state["ready"])
            self.assertEqual(served_png, png)
        finally:
            preview.close()

    def test_browser_preview_updates_selection_before_image_is_ready(self):
        png = base64.b64encode(b"old frame").decode()
        frames = ["Frame 01", "Frame 02", "Frame 03"]
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
            preview.select("Frame 01", 1, frames)
            preview.update(png)
            preview.select("Frame 03", 3, frames)

            with urlopen(preview.url + "state.json", timeout=2) as response:
                state = json.loads(response.read())
            self.assertEqual(state["frame"], "Frame 03")
            self.assertEqual(state["position"], 3)
            self.assertFalse(state["ready"])
            with self.assertRaises(HTTPError) as error:
                urlopen(preview.url + "frame.png", timeout=2)
            self.assertEqual(error.exception.code, 503)
        finally:
            preview.close()

    def test_browser_preview_accepts_navigation_commands(self):
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
            request = Request(
                preview.url + "command",
                data=json.dumps({"command": "next"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 204)
            self.assertEqual(preview.poll_commands(), ["next"])
        finally:
            preview.close()

    def test_browser_preview_validates_direct_selection(self):
        frames = ["Frame 01", "Frame 02", "Frame 03"]
        with mock.patch.dict(
            MODULE.os.environ,
            {
                "CPEN_PREVIEW_BIND": "127.0.0.1",
                "CPEN_PREVIEW_HOST": "127.0.0.1",
            },
            clear=False,
        ):
            preview = MODULE.PreviewWebServer("/tmp/design.pen")
            preview.select("Frame 01", 1, frames)
            preview.start()
        try:
            request = Request(
                preview.url + "command",
                data=json.dumps({"command": "select", "index": 2}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 204)
            self.assertEqual(preview.poll_commands(), [("select", 2)])

            for index in (-1, 3, "1", True, None):
                with self.subTest(index=index):
                    request = Request(
                        preview.url + "command",
                        data=json.dumps(
                            {"command": "select", "index": index}
                        ).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request, timeout=2)
                    self.assertEqual(error.exception.code, 400)
            self.assertEqual(preview.poll_commands(), [])
        finally:
            preview.close()

    def test_browser_preview_has_keyboard_touch_and_button_navigation(self):
        html = MODULE.PREVIEW_HTML.read_text()
        self.assertIn("id=\"previous\"", html)
        self.assertIn("id=\"next\"", html)
        self.assertIn("keydown", html)
        self.assertIn("pointerdown", html)
        self.assertIn("send(dx < 0 ? 'next' : 'previous')", html)
        self.assertIn('id="frame-list"', html)
        self.assertIn("send('select', index)", html)
        self.assertIn("nextFrameNamesKey !== frameNamesKey", html)
        self.assertIn("scrollIntoView({ block: 'nearest' })", html)
        self.assertIn("@media (min-width: 960px)", html)
        self.assertIn("width: 280px", html)
        self.assertIn("left: calc(50% + 140px)", html)

    def test_export_png_returns_desktop_image(self):
        mcp = object.__new__(MODULE.PencilMCP)
        png = b"\x89PNG\r\n\x1a\npreview"

        def export(_name, arguments):
            self.assertEqual(arguments["filePath"], "/tmp/design.pen")
            self.assertEqual(arguments["nodeIds"], ["frame"])
            return {
                "images": [
                    {"nodeId": "frame", "image": base64.b64encode(png).decode()}
                ]
            }

        with tempfile.TemporaryDirectory() as output_dir:
            with mock.patch.object(mcp, "call", side_effect=export):
                encoded = mcp.export_png("/tmp/design.pen", "frame", output_dir)
            self.assertEqual(base64.b64decode(encoded), png)
            self.assertEqual(list(Path(output_dir).iterdir()), [])

    def test_preview_cache_exports_without_blocking_selection(self):
        started = threading.Event()
        release = threading.Event()

        class MCP:
            def export_png(self, _file_path, frame_id, _output_dir):
                started.set()
                release.wait(timeout=2)
                return f"png:{frame_id}"

            def frames(self, _file_path):
                return []

            def close(self):
                release.set()

        cache = MODULE.PreviewImageCache(MCP(), "/tmp/design.pen", "/tmp")
        frame = {"id": "frame-1", "name": "Frame 01"}
        try:
            self.assertIsNone(cache.select(frame))
            self.assertTrue(started.wait(timeout=1))
            self.assertIsNone(cache.get("frame-1"))
            release.set()

            deadline = time.monotonic() + 2
            results = []
            while not results and time.monotonic() < deadline:
                results = cache.poll()
                time.sleep(0.01)

            self.assertTrue(results)
            self.assertEqual(results[0]["kind"], "image")
            self.assertEqual(cache.get("frame-1"), "png:frame-1")
        finally:
            cache.close()

    def test_preview_cache_discards_export_from_old_generation(self):
        started = threading.Event()
        release = threading.Event()
        frames = [{"id": "frame-1", "name": "Frame 01"}]

        class MCP:
            def export_png(self, _file_path, frame_id, _output_dir):
                started.set()
                release.wait(timeout=2)
                return f"stale:{frame_id}"

            def frames(self, _file_path):
                return frames

            def close(self):
                release.set()

        cache = MODULE.PreviewImageCache(MCP(), "/tmp/design.pen", "/tmp")
        try:
            cache.warm(frames, "frame-1")
            self.assertTrue(started.wait(timeout=1))
            cache.reload("frame-1")
            release.set()

            deadline = time.monotonic() + 2
            results = []
            while not results and time.monotonic() < deadline:
                results = cache.poll()
                time.sleep(0.01)

            self.assertTrue(results)
            self.assertEqual([item["kind"] for item in results], ["frames"])
            self.assertIsNone(cache.get("frame-1"))
        finally:
            cache.close()


if __name__ == "__main__":
    unittest.main()
