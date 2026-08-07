#!/usr/bin/env python3

import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "cpen_cli.py"
SPEC = importlib.util.spec_from_file_location("cpen_cli", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CpenCliTests(unittest.TestCase):
    def test_find_pen_files_uses_stdlib_and_skips_generated_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "design").mkdir()
            (root / "design" / "a.pen").touch()
            (root / "node_modules").mkdir()
            (root / "node_modules" / "ignored.pen").touch()
            self.assertEqual(MODULE.find_pen_files(root), [(root / "design" / "a.pen").resolve()])

    def test_file_option_preserves_absolute_path_prompt_and_workdir(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pen_file = root / "design space.pen"
            pen_file.touch()
            with mock.patch.object(MODULE, "git_root", return_value=root), mock.patch.object(
                MODULE, "acquire_lease", return_value=0
            ), mock.patch.object(MODULE, "release_lease") as release, mock.patch.object(
                MODULE, "open_pencil", return_value=True
            ) as open_pencil, mock.patch.object(MODULE, "bind_herdr_pane"), mock.patch.object(
                MODULE, "run_agent", return_value=0
            ) as run_agent:
                status = MODULE.main_cpen(["-a", "codex", "--file", str(pen_file), "직접", "지정"])
            self.assertEqual(status, 0)
            open_pencil.assert_called_once_with(pen_file.resolve())
            prompt = run_agent.call_args.args[-1]
            self.assertIn(f"작업 대상 .pen 파일: {pen_file.resolve()}", prompt)
            self.assertIn("filePath 에는 항상 위 절대 경로", prompt)
            self.assertIn("/rename 직접 지정", prompt)
            release.assert_called_once()

    def test_run_agent_preserves_codex_and_claude_invocations(self):
        workdir = Path("/tmp/project")
        pen_file = Path("/tmp/design.pen")
        completed = mock.Mock(returncode=0)
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(MODULE.run_agent("codex", workdir, pen_file, "pen:design", "prompt"), 0)
            codex = run.call_args
            self.assertEqual(codex.args[0], ["codex", "-C", str(workdir), "prompt"])
            self.assertEqual(codex.kwargs["cwd"], workdir)
            self.assertEqual(codex.kwargs["env"]["CPEN_PEN_FILE"], str(pen_file))
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(MODULE.run_agent("claude", workdir, pen_file, "pen:design", "prompt"), 0)
            self.assertEqual(run.call_args.args[0], ["claude", "--name", "pen:design", "prompt"])
            self.assertEqual(run.call_args.kwargs["cwd"], workdir)

    def test_skip_open_and_external_occupants_preserve_herdr_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "a.pen"
            pen_file.touch()
            environment = {"CPEN_SKIP_OPEN": "1", "CPEN_EXTERNAL_OCCUPANTS": "w1:p4"}
            with mock.patch.dict(MODULE.os.environ, environment, clear=True), mock.patch.object(
                MODULE, "git_root", return_value=Path(directory)
            ), mock.patch.object(MODULE, "acquire_lease", return_value=0), mock.patch.object(
                MODULE, "release_lease"
            ), mock.patch.object(MODULE, "open_pencil") as open_pencil, mock.patch.object(
                MODULE, "bind_herdr_pane"
            ), mock.patch.object(MODULE, "run_agent", return_value=0) as run_agent:
                status = MODULE.main_cpen(["-a", "claude", "-f", str(pen_file)])
            self.assertEqual(status, 0)
            open_pencil.assert_not_called()
            self.assertIn("연결된 Herdr pane이 있습니다: w1:p4", run_agent.call_args.args[-1])

    def test_busy_file_is_not_blocked_and_adds_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "a.pen"
            pen_file.touch()
            rows = [[str(pen_file.resolve()), "123", "codex", "pen:기존세션"]]
            with mock.patch.object(MODULE, "git_root", return_value=Path(directory)), mock.patch.object(
                MODULE, "acquire_lease", return_value=1
            ), mock.patch.object(MODULE, "occupants", return_value=rows), mock.patch.object(
                MODULE, "open_pencil", return_value=True
            ), mock.patch.object(MODULE, "bind_herdr_pane"), mock.patch.object(
                MODULE, "run_agent", return_value=0
            ) as run_agent:
                status = MODULE.main_cpen(["-a", "codex", "-f", str(pen_file)])
            self.assertEqual(status, 0)
            prompt = run_agent.call_args.args[-1]
            self.assertIn('codex(pid 123, "pen:기존세션")', prompt)
            self.assertIn("수정 직전에 대상 노드를 다시 읽고", prompt)
            self.assertNotIn("차단합니다", prompt)

    def test_missing_and_non_pen_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text_file = root / "a.txt"
            text_file.touch()
            with mock.patch.object(MODULE, "git_root", return_value=root):
                self.assertEqual(MODULE.main_cpen(["-a", "codex", "-f", str(root / "missing.pen")]), 1)
                self.assertEqual(MODULE.main_cpen(["-a", "codex", "-f", str(text_file)]), 1)

    def test_lease_release_never_removes_another_token(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "a.pen"
            pen_file.touch()
            with mock.patch.dict(MODULE.os.environ, {"CPEN_LEASE_DIR": str(Path(directory) / "leases")}, clear=True):
                cell = MODULE.lease_cell(pen_file)
                cell.mkdir(parents=True)
                (cell / "info").write_text(f"{pen_file.resolve()}\t1\tcodex\tpen:a\ttoken-a\tnow\n")
                self.assertEqual(MODULE.release_lease(pen_file, "token-b"), 1)
                self.assertTrue(cell.exists())
                self.assertEqual(MODULE.release_lease(pen_file, "token-a"), 0)
                self.assertFalse(cell.exists())

    def test_lease_alive_accepts_an_already_running_legacy_session(self):
        with tempfile.TemporaryDirectory() as directory:
            cell = Path(directory)
            (cell / "info").write_text("/tmp/a.pen\t123\tcodex\tpen:a\ttoken\tnow\n")
            completed = mock.Mock(returncode=0, stdout="/opt/homebrew/bin/fish\n")
            with mock.patch.object(MODULE.subprocess, "run", return_value=completed):
                self.assertTrue(MODULE.lease_alive(cell))

    def test_focus_legacy_hook_is_noop(self):
        with mock.patch.object(MODULE.sys, "stdin", io.StringIO("{}")), mock.patch.object(
            MODULE, "open_pencil"
        ) as open_pencil:
            self.assertEqual(MODULE.main_focus(["--if-touched"]), 0)
        open_pencil.assert_not_called()

    def test_focus_uses_environment_target(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "a.pen"
            pen_file.touch()
            with mock.patch.dict(MODULE.os.environ, {"CPEN_PEN_FILE": str(pen_file)}, clear=True), mock.patch.object(
                MODULE, "open_pencil", return_value=True
            ) as open_pencil:
                self.assertEqual(MODULE.main_focus([]), 0)
            open_pencil.assert_called_once_with(pen_file.resolve())

    def test_open_prefers_override_then_available_default_app(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "a.pen"
            pen_file.touch()
            with mock.patch.dict(
                MODULE.os.environ, {"CPEN_PENCIL_APP": "/opt/pencil-launcher"}, clear=True
            ):
                self.assertEqual(
                    MODULE.pencil_open_command(pen_file),
                    ["/opt/pencil-launcher", str(pen_file)],
                )
            with mock.patch.dict(MODULE.os.environ, {}, clear=True), mock.patch.object(
                MODULE.shutil,
                "which",
                side_effect=lambda name: "/usr/bin/open" if name == "open" else None,
            ):
                self.assertEqual(
                    MODULE.pencil_open_command(pen_file),
                    ["/usr/bin/open", str(pen_file)],
                )

    def test_save_uses_official_pencil_cli_on_every_os(self):
        with tempfile.TemporaryDirectory() as directory:
            pen_file = Path(directory) / "a.pen"
            pen_file.touch()
            completed = mock.Mock(returncode=0)
            with mock.patch.object(
                MODULE.shutil, "which", side_effect=lambda name: "/usr/bin/pen" if name == "pen" else None
            ), mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run, mock.patch(
                "sys.stdout", new_callable=io.StringIO
            ):
                self.assertEqual(MODULE.main_save([str(pen_file)]), 0)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/pen", "interactive", "--app", "desktop", "--in", str(pen_file.resolve())])
            self.assertEqual(run.call_args.kwargs["input"], "save()\nexit()\n")

    def test_hook_without_session_returns_json(self):
        with mock.patch.dict(MODULE.os.environ, {}, clear=True), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            self.assertEqual(MODULE.main_save(["--hook"]), 0)
        self.assertEqual(output.getvalue(), "{}\n")

    def test_hook_install_migrates_fish_commands_and_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "model": "opus",
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": 'fish -c "cpen-save --hook"'}]}],
                    "PreToolUse": [{"hooks": [{"type": "command", "command": "fish -c cpen-guard"}]}],
                    "PostToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}],
                },
            }))
            save_command = f"{sys.executable} /tmp/cpen-save --hook"
            self.assertEqual(MODULE.update_hook_file("install", path, save_command)[0], 0)
            self.assertEqual(MODULE.update_hook_file("install", path, save_command)[0], 0)
            document = json.loads(path.read_text())
            commands = MODULE.hook_commands(document)
            self.assertEqual([item for item in commands if "cpen-save" in item], [save_command])
            self.assertFalse(any("fish" in item for item in commands))
            self.assertIn("echo hi", commands)
            self.assertEqual(document["model"], "opus")
            self.assertTrue(Path(f"{path}.cpen-backup").is_file())

    def test_hook_uninstall_removes_only_cpen_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks.json"
            path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": "python cpen-save --hook"},
                {"type": "command", "command": "notify.sh"},
            ]}]}}))
            self.assertEqual(MODULE.update_hook_file("uninstall", path, "unused")[0], 0)
            self.assertEqual(MODULE.hook_commands(json.loads(path.read_text())), ["notify.sh"])

    def test_guard_always_passes(self):
        with mock.patch.object(MODULE.sys, "stdin", io.StringIO("not json")):
            self.assertEqual(MODULE.main_guard([]), 0)

    def test_entrypoint_runs_with_a_path_that_has_no_fish(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "cpen"), "--help"],
                env={"PATH": directory},
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: cpen", result.stdout)

    def test_runtime_files_do_not_invoke_fish(self):
        runtime = [
            ROOT / "bin" / "cpen_cli.py",
            ROOT / "bin" / "cpen",
            ROOT / "bin" / "cpen-focus",
            ROOT / "bin" / "cpen-save",
            ROOT / "bin" / "cpen-guard",
            ROOT / "herdr" / "cpen_session.py",
            ROOT / "herdr-plugin.toml",
        ]
        pattern = re.compile(r"fish\s+-c|run_cpen\.fish|[\[(][\"']fish[\"']")
        for path in runtime:
            text = path.read_text()
            self.assertIsNone(pattern.search(text), path)
            self.assertNotIn("osascript", text, path)
            self.assertNotIn("MACOS_PEN_MCP", text, path)


if __name__ == "__main__":
    unittest.main()
