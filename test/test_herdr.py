#!/usr/bin/env python3

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "herdr" / "cpen_session.py"
SPEC = importlib.util.spec_from_file_location("cpen_session", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HerdrSessionTests(unittest.TestCase):
    def test_context_values_prefer_explicit_launcher_environment(self):
        old = dict(MODULE.os.environ)
        try:
            MODULE.os.environ["CPEN_HERDR_CWD"] = "/tmp"
            MODULE.os.environ["CPEN_HERDR_WORKSPACE"] = "w9"
            self.assertEqual(
                MODULE.invocation_values(), (str(Path("/tmp").resolve()), "w9")
            )
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
                response = {
                    "type": "tool_response",
                    "data": {
                        "client_id": "client-1",
                        "request_id": request["data"]["request_id"],
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
                    MODULE.os.environ,
                    {"CPEN_PENCIL_SOCKET": socket_path},
                    clear=True,
                ):
                    mcp = MODULE.PencilMCP()
                    frames = mcp.frames("/tmp/example.pen")
                    mcp.close()
            finally:
                thread.join(timeout=2)
                server.close()

        self.assertEqual(frames, [{"id": "f1", "name": "Frame 1"}])
        self.assertEqual(received[0]["type"], "agent_connected")
        self.assertTrue(received[0]["data"]["agent"].startswith("cpenReady-"))
        self.assertEqual(
            received[1]["data"]["payload"]["filePath"], "/tmp/example.pen"
        )

    def test_prepare_pencil_desktop_opens_once_then_waits_for_exact_file(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "design.pen"
            pen_file.touch()
            command = ["/usr/bin/xdg-open", str(pen_file.resolve())]
            with mock.patch.object(
                MODULE, "pencil_open_command", return_value=command
            ), mock.patch.object(
                MODULE, "pencil_document_ready", return_value=False
            ), mock.patch.object(
                MODULE.subprocess, "Popen"
            ) as start, mock.patch.object(
                MODULE, "wait_for_pencil_desktop"
            ) as wait:
                MODULE.prepare_pencil_desktop(str(pen_file))
        start.assert_called_once_with(
            command,
            stdin=MODULE.subprocess.DEVNULL,
            stdout=MODULE.subprocess.DEVNULL,
            stderr=MODULE.subprocess.DEVNULL,
            start_new_session=True,
        )
        wait.assert_called_once_with(str(pen_file.resolve()))

    def test_prepare_uses_ready_document_without_relaunching_pen(self):
        with mock.patch.object(
            MODULE, "pencil_document_ready", return_value=True
        ) as ready, mock.patch.object(MODULE.subprocess, "Popen") as start, mock.patch.object(
            MODULE, "wait_for_pencil_desktop"
        ) as wait:
            MODULE.prepare_pencil_desktop("/tmp/design.pen")
        ready.assert_called_once_with(str(Path("/tmp/design.pen").resolve()))
        start.assert_not_called()
        wait.assert_not_called()

    def test_pen_desktop_uses_file_flag_required_for_cold_start(self):
        with mock.patch.object(
            MODULE.shutil,
            "which",
            side_effect=lambda name: (
                "/usr/bin/pen-desktop" if name == "pen-desktop" else None
            ),
        ):
            self.assertEqual(
                MODULE.pencil_open_command("/tmp/design.pen"),
                [
                    "/usr/bin/pen-desktop",
                    "--file",
                    str(Path("/tmp/design.pen").resolve()),
                ],
            )

    def test_desktop_readiness_queries_the_selected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "design.pen"
            pen_file.touch()
            mcp = mock.Mock()
            mcp.frames.return_value = []
            with mock.patch.object(MODULE, "PencilMCP", return_value=mcp):
                MODULE.wait_for_pencil_desktop(str(pen_file), timeout=0)
        mcp.frames.assert_called_once_with(str(pen_file.resolve()))
        mcp.close.assert_called_once_with()

    def test_document_ready_uses_a_short_socket_timeout(self):
        mcp = mock.Mock()
        with mock.patch.object(
            MODULE, "pencil_socket_path", return_value=Path(__file__)
        ), mock.patch.object(MODULE, "PencilMCP", return_value=mcp) as client:
            self.assertTrue(MODULE.pencil_document_ready("/tmp/design.pen"))
        client.assert_called_once_with(timeout=1)
        mcp.frames.assert_called_once_with(str(Path("/tmp/design.pen").resolve()))
        mcp.close.assert_called_once_with()

    def test_launch_prepares_desktop_then_starts_one_agent_pane(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "CPEN_HERDR_CWD": str(root),
                "CPEN_HERDR_WORKSPACE": "w1",
            }
            events = []

            def run_json(*args):
                events.append(args[:2])
                if args[:2] == ("tab", "create"):
                    return {
                        "result": {
                            "tab": {"tab_id": "w1:t2"},
                            "root_pane": {"pane_id": "w1:p2"},
                        }
                    }
                if args[:2] == ("tab", "focus"):
                    return {"result": {}}
                raise AssertionError(args)

            with mock.patch.dict(
                MODULE.os.environ, environment, clear=False
            ), mock.patch.object(
                MODULE,
                "choose",
                side_effect=[f"{pen_file.resolve()}\tdesign.pen", "codex"],
            ), mock.patch.object(
                MODULE.shutil,
                "which",
                side_effect=lambda name: f"/usr/bin/{name}",
            ), mock.patch.object(
                MODULE,
                "prepare_pencil_desktop",
                side_effect=lambda path: events.append(("desktop", path)),
            ) as prepare, mock.patch.object(
                MODULE, "run_json", side_effect=run_json
            ), mock.patch.object(
                MODULE,
                "pane_run",
                side_effect=lambda pane, command: events.append(("agent", command)),
            ) as pane_run:
                self.assertEqual(MODULE.launch_session(), 0)

            order = [
                ".".join(event) if event[0] == "tab" else event[0]
                for event in events
            ]
            self.assertEqual(order, ["desktop", "tab.create", "agent", "tab.focus"])
            prepare.assert_called_once_with(str(pen_file.resolve()))
            pane_run.assert_called_once()
            self.assertEqual(pane_run.call_args.args[0], "w1:p2")
            self.assertEqual(
                pane_run.call_args.args[1][:2], ["env", "CPEN_SKIP_OPEN=1"]
            )
            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                binding = MODULE.read_binding("w1:p2")
            self.assertEqual(binding["pen_file"], str(pen_file.resolve()))
            self.assertNotIn("preview_pane_id", binding)

    def test_missing_workspace_exits_cleanly_without_a_dead_popup(self):
        with mock.patch.object(
            MODULE, "invocation_values", return_value=("/tmp", "")
        ), mock.patch.object(MODULE.time, "sleep") as sleep, mock.patch(
            "sys.stderr", new_callable=io.StringIO
        ) as error:
            self.assertEqual(MODULE.launch_session(), 0)
        self.assertIn("workspace", error.getvalue())
        sleep.assert_called_once_with(3)

    def test_handled_launch_error_exits_cleanly_without_a_dead_popup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "CPEN_HERDR_CWD": str(root),
                "CPEN_HERDR_WORKSPACE": "w1",
            }
            with mock.patch.dict(
                MODULE.os.environ, environment, clear=False
            ), mock.patch.object(
                MODULE,
                "choose",
                side_effect=[f"{pen_file.resolve()}\tdesign.pen", "codex"],
            ), mock.patch.object(
                MODULE.shutil, "which", return_value="/usr/bin/codex"
            ), mock.patch.object(
                MODULE,
                "prepare_pencil_desktop",
                side_effect=RuntimeError("desktop socket unavailable"),
            ), mock.patch.object(
                MODULE, "run_json"
            ) as run_json, mock.patch.object(
                MODULE, "pane_run"
            ) as pane_run, mock.patch.object(
                MODULE.time, "sleep"
            ), mock.patch(
                "sys.stderr", new_callable=io.StringIO
            ) as error:
                self.assertEqual(MODULE.launch_session(), 0)
            self.assertIn("desktop socket unavailable", error.getvalue())
            run_json.assert_not_called()
            pane_run.assert_not_called()

    def test_pane_binding_persists_file_without_preview_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            with mock.patch.dict(
                MODULE.os.environ, {"CPEN_BINDING_DIR": str(root / "bindings")}
            ):
                MODULE.write_binding("w1:p2", str(pen_file))
                binding = MODULE.read_binding("w1:p2")
        self.assertEqual(binding["pen_file"], str(pen_file.resolve()))
        self.assertNotIn("preview_pane_id", binding)

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
                MODULE.write_binding("w2:p7", str(pen_file))
                row = MODULE.pen_file_rows([str(pen_file)], str(root))[0]
        self.assertIn("w1:p2", row)
        self.assertIn("w2:p7", row)

    def test_moving_pane_moves_its_file_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "HERDR_PLUGIN_EVENT": "pane.moved",
                "HERDR_PLUGIN_EVENT_JSON": json.dumps(
                    {"previous_pane_id": "w1:p2", "pane_id": "w1:p9"}
                ),
            }
            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                MODULE.write_binding("w1:p2", str(pen_file))
                self.assertEqual(MODULE.handle_plugin_event(), 0)
                self.assertIsNone(MODULE.read_binding("w1:p2"))
                self.assertEqual(
                    MODULE.read_binding("w1:p9")["pen_file"],
                    str(pen_file.resolve()),
                )

    def test_closed_pane_removes_its_file_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design.pen"
            pen_file.touch()
            environment = {
                "CPEN_BINDING_DIR": str(root / "bindings"),
                "HERDR_PLUGIN_EVENT": "pane.closed",
                "HERDR_PLUGIN_EVENT_JSON": json.dumps({"pane_id": "w1:p2"}),
            }
            with mock.patch.dict(MODULE.os.environ, environment, clear=False):
                MODULE.write_binding("w1:p2", str(pen_file))
                self.assertEqual(MODULE.handle_plugin_event(), 0)
                self.assertIsNone(MODULE.read_binding("w1:p2"))

    def test_pane_run_accepts_empty_stdout(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed):
            MODULE.run_cli("pane", "run", "w1:p2", "true")

    def test_pane_run_passes_one_shell_command(self):
        with mock.patch.object(MODULE, "run_cli") as run_cli:
            MODULE.pane_run("w1:p2", ["python3", "/tmp/cpen", "a b"])
        run_cli.assert_called_once_with(
            "pane", "run", "w1:p2", "python3 /tmp/cpen 'a b'"
        )

    def test_agent_command_runs_the_python_entrypoint_without_a_shell(self):
        command = MODULE.agent_command("codex", "/tmp/a b.pen", "a b")
        self.assertEqual(command[:2], ["env", "CPEN_SKIP_OPEN=1"])
        self.assertEqual(command[2], MODULE.sys.executable)
        self.assertEqual(command[3], str(MODULE.CPEN_RUNNER))
        self.assertNotIn("fish", command)
        self.assertEqual(
            command[-4:], ["codex", "--file", "/tmp/a b.pen", "pen:a b"]
        )

    def test_agent_command_passes_external_binding_occupants(self):
        command = MODULE.agent_command(
            "codex", "/tmp/a.pen", "a", "w1:p2, w1:p4"
        )
        self.assertEqual(
            command[:3],
            [
                "env",
                "CPEN_SKIP_OPEN=1",
                "CPEN_EXTERNAL_OCCUPANTS=w1:p2, w1:p4",
            ],
        )
        self.assertEqual(command[3], MODULE.sys.executable)

    def test_find_pen_files_uses_stdlib_and_skips_generated_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "design").mkdir()
            (root / "design" / "a.pen").touch()
            (root / "build").mkdir()
            (root / "build" / "ignored.pen").touch()
            self.assertEqual(
                MODULE.find_pen_files(str(root)),
                [str((root / "design" / "a.pen").resolve())],
            )

    def test_manifest_does_not_expose_preview_actions_or_panes(self):
        manifest = (ROOT / "herdr-plugin.toml").read_text()
        self.assertEqual(manifest.count("[[actions]]"), 1)
        self.assertEqual(manifest.count("[[panes]]"), 1)
        self.assertNotIn("preview", manifest.lower())
        self.assertNotIn("attach", manifest.lower())
        self.assertNotIn("detach", manifest.lower())


if __name__ == "__main__":
    unittest.main()
