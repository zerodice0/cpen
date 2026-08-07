#!/usr/bin/env python3
"""Small Herdr launcher and read-only Pencil preview for cpen."""

from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import termios
import tempfile
import threading
import time
import tty
from urllib.parse import urlparse
import uuid


PLUGIN_ID = "zerodice0.cpen"
PLUGIN_VERSION = "0.6.0"
CPEN_RUNNER = Path(__file__).resolve().parents[1] / "bin" / "cpen"
FRAME_QUERY = (
    'Get(document,(n,c)=>c.depth===0 && n.type==="frame" && !n.reusable '
    '&& Print(JSON.stringify({id:n.id,name:n.name||"Untitled"})))'
)
PEN_FILE_PATTERN = re.compile(r"작업 대상 \.pen 파일: ([^\n]+)")
PREVIEW_HTML = Path(__file__).with_name("preview.html")
BINDING_VERSION = 1


def context() -> dict:
    try:
        return json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}"))
    except json.JSONDecodeError:
        return {}


def binding_dir() -> Path:
    explicit = os.environ.get("CPEN_BINDING_DIR", "")
    if explicit:
        return Path(explicit)
    plugin_state = os.environ.get("HERDR_PLUGIN_STATE_DIR", "")
    if plugin_state:
        return Path(plugin_state) / "bindings"
    return shared_binding_dir()


def shared_binding_dir() -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "cpen" / "herdr-bindings"


def binding_dirs() -> list[Path]:
    if os.environ.get("CPEN_BINDING_DIR", ""):
        return [binding_dir()]
    roots = [binding_dir(), shared_binding_dir()]
    return list(dict.fromkeys(roots))


def binding_path(pane_id: str, root: Path | None = None) -> Path:
    key = hashlib.sha256(pane_id.encode()).hexdigest()[:16]
    return (root or binding_dir()) / f"{key}.json"


def read_binding(pane_id: str) -> dict | None:
    if not pane_id:
        return None
    for root in binding_dirs():
        try:
            binding = json.loads(binding_path(pane_id, root).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if (
            binding.get("version") == BINDING_VERSION
            and binding.get("pane_id") == pane_id
            and binding.get("pen_file")
        ):
            return binding
    return None


def write_binding(
    pane_id: str,
    pen_file: str,
    preview_pane_id: str | None = None,
) -> dict:
    current = read_binding(pane_id) or {}
    binding = {
        "version": BINDING_VERSION,
        "pane_id": pane_id,
        "pen_file": str(Path(pen_file).resolve()),
        "preview_pane_id": (
            current.get("preview_pane_id", "")
            if preview_pane_id is None
            else preview_pane_id
        ),
        "attached_unix_ms": current.get(
            "attached_unix_ms", int(time.time() * 1000)
        ),
    }
    root = binding_dir()
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".binding-", dir=root)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(binding, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary_path, binding_path(pane_id))
        for alternate in binding_dirs()[1:]:
            binding_path(pane_id, alternate).unlink(missing_ok=True)
    finally:
        temporary_path.unlink(missing_ok=True)
    return binding


def remove_binding(pane_id: str) -> None:
    if pane_id:
        for root in binding_dirs():
            binding_path(pane_id, root).unlink(missing_ok=True)


def bindings() -> list[dict]:
    found = []
    seen = set()
    for root in binding_dirs():
        for path in root.glob("*.json"):
            try:
                binding = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            pane_id = binding.get("pane_id")
            if (
                binding.get("version") == BINDING_VERSION
                and pane_id
                and pane_id not in seen
                and binding.get("pen_file")
            ):
                seen.add(pane_id)
                found.append(binding)
    return found


def bindings_for_file(pen_file: str, exclude_pane: str = "") -> list[dict]:
    target = str(Path(pen_file).resolve())
    return [
        binding
        for binding in bindings()
        if binding["pen_file"] == target and binding["pane_id"] != exclude_pane
    ]


def binding_for_preview(preview_pane_id: str) -> dict | None:
    return next(
        (
            binding
            for binding in bindings()
            if binding.get("preview_pane_id") == preview_pane_id
        ),
        None,
    )


def set_binding_preview(pane_id: str, preview_pane_id: str) -> None:
    binding = read_binding(pane_id)
    if binding:
        write_binding(pane_id, binding["pen_file"], preview_pane_id)


def move_binding(previous_pane_id: str, pane_id: str) -> None:
    source = read_binding(previous_pane_id)
    if source:
        write_binding(pane_id, source["pen_file"], source.get("preview_pane_id", ""))
        remove_binding(previous_pane_id)
    for binding in bindings():
        if binding.get("preview_pane_id") == previous_pane_id:
            write_binding(binding["pane_id"], binding["pen_file"], pane_id)


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


def run_json(*args: str) -> dict:
    result = subprocess.run(
        [herdr_bin(), *args], text=True, capture_output=True, check=True
    )
    return json.loads(result.stdout)


def run_cli(*args: str) -> None:
    subprocess.run(
        [herdr_bin(), *args], text=True, capture_output=True, check=True
    )


def pane_run(pane_id: str, command: list[str]) -> None:
    run_cli("pane", "run", pane_id, shlex.join(command))


def agent_command(
    agent: str,
    pen_file: str,
    stem: str,
    external_occupants: str = "",
    binding_root: str = "",
) -> list[str]:
    command = [
        sys.executable,
        str(CPEN_RUNNER),
        "-a",
        agent,
        "--file",
        pen_file,
        f"pen:{stem}",
    ]
    environment = ["CPEN_SKIP_OPEN=1"]
    if external_occupants:
        environment.append(f"CPEN_EXTERNAL_OCCUPANTS={external_occupants}")
    if binding_root:
        environment.append(f"CPEN_BINDING_DIR={binding_root}")
    if environment:
        return ["env", *environment, *command]
    return command


def invocation_values() -> tuple[str, str]:
    data = context()
    cwd = (
        os.environ.get("CPEN_HERDR_CWD")
        or data.get("focused_pane_cwd")
        or data.get("workspace_cwd")
        or os.getcwd()
    )
    workspace = os.environ.get("CPEN_HERDR_WORKSPACE") or os.environ.get(
        "HERDR_WORKSPACE_ID", ""
    )
    if not workspace:
        pane_id = data.get("focused_pane_id", "")
        workspace = pane_id.split(":", 1)[0] if ":" in pane_id else ""
    return str(Path(cwd).resolve()), workspace


def open_launcher() -> int:
    command = [
        herdr_bin(),
        "plugin",
        "pane",
        "open",
        "--plugin",
        PLUGIN_ID,
        "--entrypoint",
        "launcher",
        "--focus",
    ]
    return subprocess.run(command).returncode


def open_attach_launcher(pane_id: str) -> int:
    command = [
        herdr_bin(),
        "plugin",
        "pane",
        "open",
        "--plugin",
        PLUGIN_ID,
        "--entrypoint",
        "attach",
        "--target-pane",
        pane_id,
        "--focus",
    ]
    return subprocess.run(command).returncode


def choose(prompt: str, rows: list[str], *, delimiter: bool = False) -> str:
    command = ["fzf", "--reverse", "--height=100%", f"--prompt={prompt}> "]
    if delimiter:
        command += ["--delimiter=\t", "--with-nth=2.."]
    result = subprocess.run(
        command, input="\n".join(rows) + "\n", text=True, capture_output=True
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def find_pen_files(root: str) -> list[str]:
    excluded = {"build", ".dart_tool", "node_modules", "Pods", ".git", "DerivedData"}
    paths = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in excluded]
        paths.extend(
            str((Path(directory) / name).resolve())
            for name in files
            if name.endswith(".pen")
        )
    return sorted(paths)


def pen_file_rows(paths: list[str], root: str, exclude_pane: str = "") -> list[str]:
    rows = []
    for path in paths:
        label = os.path.relpath(path, root)
        occupants = bindings_for_file(path, exclude_pane)
        if occupants:
            panes = ", ".join(binding["pane_id"] for binding in occupants)
            label += f"  ⚠ 연결됨: {panes}"
        rows.append(f"{path}\t{label}")
    return rows


def wait_for_ready(path: Path, timeout: float = 22) -> bool:
    if path.exists():
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def start_preview(pane_id: str, pen_file: str, source_pane: str = "") -> None:
    script = str(Path(__file__).resolve())
    ready_path = Path(tempfile.gettempdir()) / f"cpen-preview-{uuid.uuid4()}.ready"
    try:
        command = [sys.executable, script, "preview", pen_file, str(ready_path)]
        if source_pane:
            command.append(source_pane)
        pane_run(pane_id, command)
        if not wait_for_ready(ready_path):
            raise RuntimeError("Pencil preview 연결 시간이 초과됐습니다")
    finally:
        ready_path.unlink(missing_ok=True)


def pane_process_info(pane_id: str) -> dict:
    return run_json("pane", "process-info", "--pane", pane_id)["result"][
        "process_info"
    ]


def pen_file_from_process_info(process_info: dict) -> str:
    for process in process_info.get("foreground_processes", []):
        values = [process.get("cmdline", ""), *(process.get("argv") or [])]
        for value in values:
            match = PEN_FILE_PATTERN.search(str(value))
            if match:
                return str(Path(match.group(1)).resolve())
    return ""


def pen_file_from_lease(session_label: str, lease_dir: Path | None = None) -> str:
    if not session_label:
        return ""
    root = lease_dir or Path(
        os.environ.get("CPEN_LEASE_DIR")
        or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "cpen"
        / "leases"
    )
    matches: list[tuple[float, str]] = []
    for info_path in root.glob("*.d/info"):
        try:
            fields = info_path.read_text().rstrip("\n").split("\t")
            pen_file = str(Path(fields[0]).resolve())
            if len(fields) >= 4 and fields[3] == session_label and Path(pen_file).is_file():
                matches.append((info_path.stat().st_mtime, pen_file))
        except (IndexError, OSError):
            continue
    return max(matches, default=(0, ""))[1]


def pen_file_from_pane(pane: dict, process_info: dict) -> str:
    return pen_file_from_process_info(process_info) or pen_file_from_lease(
        str(pane.get("terminal_title_stripped") or "")
    )


def preview_source_from_process_info(process_info: dict) -> str:
    for process in process_info.get("foreground_processes", []):
        argv = process.get("argv") or []
        if "preview" not in argv:
            continue
        index = argv.index("preview")
        if len(argv) > index + 3:
            return str(argv[index + 3])
    return ""


def stop_preview(pane_id: str) -> None:
    run_cli("pane", "send-text", pane_id, "q")


def focused_pane_values() -> tuple[str, str]:
    data = context()
    focused_id = str(
        data.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID") or ""
    )
    workspace = str(
        data.get("workspace_id") or os.environ.get("HERDR_WORKSPACE_ID") or ""
    )
    if not workspace and ":" in focused_id:
        workspace = focused_id.split(":", 1)[0]
    return focused_id, workspace


def toggle_preview() -> int:
    focused_id, workspace = focused_pane_values()
    if not focused_id or not workspace:
        print("cpen: focused Herdr pane을 확인할 수 없습니다.", file=sys.stderr)
        return 1

    preview_id = ""
    try:
        focused_info = pane_process_info(focused_id)
        focused_preview_source = preview_source_from_process_info(focused_info)
        if focused_preview_source:
            set_binding_preview(focused_preview_source, "")
            stop_preview(focused_id)
            return 0

        panes = run_json("pane", "list", "--workspace", workspace)["result"][
            "panes"
        ]
        source_pane = next(
            (pane for pane in panes if pane.get("pane_id") == focused_id), None
        )
        if not source_pane:
            raise RuntimeError("focused pane 정보를 찾지 못했습니다")

        binding = read_binding(focused_id)
        pen_file = str(binding.get("pen_file", "")) if binding else ""
        if pen_file and not Path(pen_file).is_file():
            remove_binding(focused_id)
            binding = None
            pen_file = ""
        if not pen_file:
            pen_file = pen_file_from_pane(source_pane, focused_info)
            if pen_file:
                binding = write_binding(focused_id, pen_file)
        if not pen_file:
            return open_attach_launcher(focused_id)

        pane_ids = {
            str(pane.get("pane_id") or "")
            for pane in panes
            if pane.get("pane_id")
        }
        bound_preview_id = str(binding.get("preview_pane_id", "")) if binding else ""
        if bound_preview_id:
            set_binding_preview(focused_id, "")
            if bound_preview_id in pane_ids:
                try:
                    bound_preview_info = pane_process_info(bound_preview_id)
                except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError):
                    bound_preview_info = {}
                if preview_source_from_process_info(bound_preview_info) == focused_id:
                    stop_preview(bound_preview_id)
                    return 0
                subprocess.run(
                    [herdr_bin(), "pane", "close", bound_preview_id],
                    capture_output=True,
                    check=False,
                )

        for pane in panes:
            if pane.get("tab_id") != source_pane.get("tab_id"):
                continue
            candidate_id = str(pane.get("pane_id") or "")
            if not candidate_id or candidate_id == focused_id:
                continue
            try:
                candidate_info = pane_process_info(candidate_id)
            except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError):
                continue
            if preview_source_from_process_info(candidate_info) == focused_id:
                set_binding_preview(focused_id, "")
                stop_preview(candidate_id)
                return 0

        split = run_json(
            "pane",
            "split",
            focused_id,
            "--direction",
            "right",
            "--ratio",
            "0.6666667",
            "--cwd",
            str(source_pane.get("cwd") or Path(pen_file).parent),
            "--no-focus",
        )["result"]
        preview_id = split["pane"]["pane_id"]
        run_cli("pane", "rename", preview_id, f"Pencil · {Path(pen_file).stem}")
        start_preview(preview_id, pen_file, focused_id)
        set_binding_preview(focused_id, preview_id)
        return 0
    except (
        subprocess.CalledProcessError,
        KeyError,
        json.JSONDecodeError,
        RuntimeError,
    ) as error:
        if preview_id:
            subprocess.run(
                [herdr_bin(), "pane", "close", preview_id], capture_output=True
            )
        detail = getattr(error, "stderr", "") or str(error)
        print(f"cpen: 미리보기를 전환하지 못했습니다: {detail.strip()}", file=sys.stderr)
        time.sleep(3)
        return 1


def attach_preview() -> int:
    focused_id, workspace = focused_pane_values()
    if not focused_id or not workspace:
        print("cpen: focused Herdr pane을 확인할 수 없습니다.", file=sys.stderr)
        return 1
    try:
        panes = run_json("pane", "list", "--workspace", workspace)["result"][
            "panes"
        ]
        source_pane = next(
            (pane for pane in panes if pane.get("pane_id") == focused_id), None
        )
        if not source_pane:
            raise RuntimeError("focused pane 정보를 찾지 못했습니다")
        root = str(source_pane.get("cwd") or os.getcwd())
        paths = find_pen_files(root)
        if not paths:
            raise RuntimeError(f".pen 파일이 없습니다: {root}")
        picked = choose(
            "Pencil file",
            pen_file_rows(paths, root, focused_id),
            delimiter=True,
        )
        if not picked:
            return 0
        pen_file = picked.split("\t", 1)[0]
        previous = read_binding(focused_id)
        previous_preview = str(previous.get("preview_pane_id", "")) if previous else ""
        if previous_preview:
            try:
                stop_preview(previous_preview)
            except subprocess.CalledProcessError:
                pass
        write_binding(focused_id, pen_file, "")
        return toggle_preview()
    except (
        subprocess.CalledProcessError,
        KeyError,
        json.JSONDecodeError,
        RuntimeError,
    ) as error:
        detail = getattr(error, "stderr", "") or str(error)
        print(f"cpen: Pencil 파일을 연결하지 못했습니다: {detail.strip()}", file=sys.stderr)
        time.sleep(3)
        return 1


def detach_pane() -> int:
    focused_id, _workspace = focused_pane_values()
    if not focused_id:
        print("cpen: focused Herdr pane을 확인할 수 없습니다.", file=sys.stderr)
        return 1
    binding = read_binding(focused_id)
    if not binding:
        return 0
    preview_id = str(binding.get("preview_pane_id", ""))
    remove_binding(focused_id)
    if preview_id:
        try:
            stop_preview(preview_id)
        except subprocess.CalledProcessError:
            pass
    return 0


def launch_session() -> int:
    cwd, workspace = invocation_values()
    if not workspace:
        print("cpen: Herdr workspace를 확인할 수 없습니다.", file=sys.stderr)
        return 1

    paths = find_pen_files(cwd)
    if not paths:
        print(f"cpen: .pen 파일이 없습니다: {cwd}", file=sys.stderr)
        time.sleep(2)
        return 1
    rows = pen_file_rows(paths, cwd)
    picked = choose("Pencil file", rows, delimiter=True)
    if not picked:
        return 0
    pen_file = picked.split("\t", 1)[0]

    agents = [name for name in ("codex", "claude") if shutil.which(name)]
    agent = choose("Agent", agents)
    if not agent:
        return 0

    stem = Path(pen_file).stem
    label = f"Pencil · {stem}"
    external_occupants = ", ".join(
        binding["pane_id"] for binding in bindings_for_file(pen_file)
    )
    tab_id = ""
    left_id = ""
    try:
        created = run_json(
            "tab", "create", "--workspace", workspace, "--cwd", cwd,
            "--label", label, "--no-focus",
        )["result"]
        tab_id = created["tab"]["tab_id"]
        left_id = created["root_pane"]["pane_id"]
        split = run_json(
            "pane", "split", left_id, "--direction", "right", "--ratio", "0.6666667",
            "--cwd", cwd, "--no-focus",
        )["result"]
        right_id = split["pane"]["pane_id"]

        write_binding(left_id, pen_file, right_id)
        start_preview(right_id, pen_file)
        pane_run(
            left_id,
            agent_command(
                agent,
                pen_file,
                stem,
                external_occupants,
                str(binding_dir()),
            ),
        )
        run_json("tab", "focus", tab_id)
        return 0
    except (
        subprocess.CalledProcessError,
        KeyError,
        json.JSONDecodeError,
        RuntimeError,
    ) as error:
        detail = getattr(error, "stderr", "") or str(error)
        print(f"cpen: Pencil 탭을 열지 못했습니다: {detail.strip()}", file=sys.stderr)
        if left_id:
            remove_binding(left_id)
        if tab_id:
            subprocess.run([herdr_bin(), "tab", "close", tab_id], capture_output=True)
        time.sleep(3)
        return 1


class PencilMCP:
    def __init__(self) -> None:
        path = os.environ.get("CPEN_PENCIL_SOCKET") or str(
            Path.home() / ".pencil/socket/pencil-desktop.sock"
        )
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(20)
        self.buffer = b""
        try:
            self.socket.connect(path)
            assignment = self.receive()
            data = assignment.get("data", {})
            if (
                assignment.get("type") != "tool_response"
                or data.get("request_id") != "client-id-assignment"
                or not data.get("success")
                or not data.get("client_id")
            ):
                raise RuntimeError("Pencil desktop client ID를 받지 못했습니다")
            self.client_id = data["client_id"]
            self.send(
                {
                    "type": "agent_connected",
                    "data": {
                        "client_id": self.client_id,
                        "agent": f"cpenPreview-{uuid.uuid4().hex[:8]}",
                    },
                }
            )
        except Exception:
            self.close()
            raise

    def send(self, message: dict) -> None:
        self.socket.sendall(json.dumps(message).encode() + b"\f")

    def receive(self) -> dict:
        while b"\f" not in self.buffer:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise RuntimeError("Pencil desktop 연결이 종료되었습니다")
            self.buffer += chunk
        raw, self.buffer = self.buffer.split(b"\f", 1)
        return json.loads(raw)

    def call(self, name: str, arguments: dict) -> dict:
        request_id = str(uuid.uuid4())
        self.send(
            {
                "type": "tool_request",
                "data": {
                    "client_id": self.client_id,
                    "request_id": request_id,
                    "name": name.replace("_", "-"),
                    "payload": arguments,
                },
            }
        )
        while True:
            message = self.receive()
            data = message.get("data", {})
            if (
                message.get("type") != "tool_response"
                or data.get("request_id") != request_id
            ):
                continue
            if not data.get("success"):
                raise RuntimeError(data.get("error") or "Pencil desktop 오류")
            return data.get("result") or {}

    def frames(self, file_path: str) -> list[dict]:
        result = self.call("execute", {"filePath": file_path, "input": FRAME_QUERY})
        text = result.get("message", "")
        frames = []
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("id"):
                frames.append(item)
        return frames

    def export_png(self, file_path: str, node_id: str, output_dir: str) -> str:
        result = self.call(
            "export_nodes",
            {
                "filePath": file_path,
                "nodeIds": [node_id],
                "outputDir": output_dir,
                "format": "png",
                "scale": 2,
            },
        )
        images = result.get("images", [])
        if len(images) != 1 or not images[0].get("image"):
            raise RuntimeError("Pencil desktop이 PNG 이미지 하나를 반환하지 않았습니다")
        return images[0]["image"]

    def close(self) -> None:
        self.socket.close()


class PreviewImageCache:
    def __init__(self, mcp: PencilMCP, file_path: str, output_dir: str) -> None:
        self.mcp = mcp
        self.file_path = file_path
        self.output_dir = output_dir
        self.images: dict[str, str] = {}
        self.generation = 1
        self.wanted_frame = ""
        self.sequence = 0
        self.closed = False
        self.lock = threading.Lock()
        self.jobs: queue.PriorityQueue[tuple] = queue.PriorityQueue()
        self.results: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self.thread = threading.Thread(
            target=self._run, name="cpen-preview-export", daemon=True
        )
        self.thread.start()

    def _next_sequence(self) -> int:
        with self.lock:
            self.sequence += 1
            return self.sequence

    def _schedule(self, frame: dict, urgent: bool, generation: int) -> None:
        sequence = self._next_sequence()
        priority = 0 if urgent else 1
        order = -sequence if urgent else sequence
        self.jobs.put(
            (
                priority,
                order,
                "image",
                generation,
                str(frame["id"]),
                str(frame["name"]),
            )
        )

    def seed(self, frame_id: str, png: str) -> None:
        with self.lock:
            self.images[frame_id] = png

    def warm(
        self,
        frames: list[dict],
        selected_id: str,
        generation: int | None = None,
    ) -> None:
        with self.lock:
            current_generation = self.generation
            if generation is not None and generation != current_generation:
                return
            self.wanted_frame = selected_id
        for frame in frames:
            if frame["id"] == selected_id:
                self._schedule(frame, True, current_generation)
            else:
                self._schedule(frame, False, current_generation)

    def select(self, frame: dict) -> str | None:
        frame_id = str(frame["id"])
        with self.lock:
            self.wanted_frame = frame_id
            png = self.images.get(frame_id)
            generation = self.generation
        if png is None:
            self._schedule(frame, True, generation)
        return png

    def reload(self, selected_id: str) -> None:
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.images.clear()
            self.wanted_frame = selected_id
        self.jobs.put(
            (-1, self._next_sequence(), "reload", generation, selected_id, "")
        )

    def get(self, frame_id: str) -> str | None:
        with self.lock:
            return self.images.get(frame_id)

    def poll(self) -> list[dict]:
        items = []
        while True:
            try:
                item = self.results.get_nowait()
                with self.lock:
                    if item["generation"] == self.generation:
                        items.append(item)
            except queue.Empty:
                return items

    def _run(self) -> None:
        while True:
            priority, _order, kind, generation, frame_id, frame_name = self.jobs.get()
            if kind == "close":
                return
            with self.lock:
                if self.closed or generation != self.generation:
                    continue
                if kind == "image":
                    if frame_id in self.images:
                        continue
                    if priority == 0 and frame_id != self.wanted_frame:
                        continue
            try:
                if kind == "reload":
                    frames = self.mcp.frames(self.file_path)
                    result = {
                        "kind": "frames",
                        "generation": generation,
                        "frames": frames,
                        "selected_id": frame_id,
                    }
                else:
                    png = self.mcp.export_png(
                        self.file_path, frame_id, self.output_dir
                    )
                    with self.lock:
                        if self.closed or generation != self.generation:
                            continue
                        self.images[frame_id] = png
                    result = {
                        "kind": "image",
                        "generation": generation,
                        "frame_id": frame_id,
                        "frame_name": frame_name,
                        "png": png,
                    }
            except (OSError, RuntimeError, json.JSONDecodeError) as error:
                with self.lock:
                    if self.closed:
                        return
                result = {
                    "kind": "error",
                    "generation": generation,
                    "message": str(error),
                }
            self.results.put(result)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.generation += 1
        self.jobs.put((-2, self._next_sequence(), "close", 0, "", ""))
        self.mcp.close()
        self.thread.join(timeout=2)


def herdr_request(method: str, params: dict) -> dict:
    path = os.environ.get("HERDR_SOCKET_PATH", "")
    if not path:
        raise RuntimeError("HERDR_SOCKET_PATH가 없습니다")
    request_id = f"cpen:{uuid.uuid4()}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(path)
        connection.sendall((json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode())
        response = b""
        while not response.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                break
            response += chunk
    message = json.loads(response)
    if "error" in message:
        raise RuntimeError(message["error"].get("message", str(message["error"])))
    return message.get("result", {})


def tailscale_identity() -> tuple[str, str] | None:
    explicit_bind = os.environ.get("CPEN_PREVIEW_BIND", "")
    if explicit_bind:
        return explicit_bind, os.environ.get("CPEN_PREVIEW_HOST", explicit_bind)

    binary = shutil.which("tailscale")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "status", "--json"],
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        status = json.loads(result.stdout)
        if status.get("BackendState") != "Running":
            return None
        self_status = status.get("Self") or {}
        address = next(
            (
                item
                for item in self_status.get("TailscaleIPs", [])
                if str(item).startswith("100.")
            ),
            "",
        )
        if not address:
            return None
        dns_name = str(self_status.get("DNSName") or address).rstrip(".")
        return address, dns_name
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        return None


class PreviewWebServer:
    def __init__(self, file_path: str) -> None:
        self.file_name = Path(file_path).name
        self.frame_name = ""
        self.frame_names: list[str] = []
        self.png = b""
        self.revision = 0
        self.token = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.server = None
        self.thread = None
        self.url = ""
        self.network = ""
        self.tunnel_command = ""
        self.position = 0
        self.count = 0
        self.commands: queue.Queue[str | tuple[str, int]] = queue.Queue()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        preview = self
        root = f"/cpen/{self.token}"

        class Handler(BaseHTTPRequestHandler):
            def send_bytes(self, status: int, content_type: str, data: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path in (root, root + "/"):
                    self.send_bytes(
                        200, "text/html; charset=utf-8", PREVIEW_HTML.read_bytes()
                    )
                    return
                if path == root + "/state.json":
                    with preview.lock:
                        payload = {
                            "file": preview.file_name,
                            "frame": preview.frame_name,
                            "revision": preview.revision,
                            "ready": bool(preview.png),
                            "position": preview.position,
                            "count": preview.count,
                            "frames": preview.frame_names,
                        }
                    self.send_bytes(
                        200,
                        "application/json; charset=utf-8",
                        json.dumps(payload, ensure_ascii=False).encode(),
                    )
                    return
                if path == root + "/frame.png":
                    with preview.lock:
                        png = preview.png
                    if not png:
                        self.send_bytes(503, "text/plain; charset=utf-8", b"loading")
                    else:
                        self.send_bytes(200, "image/png", png)
                    return
                self.send_bytes(404, "text/plain; charset=utf-8", b"not found")

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path != root + "/command":
                    self.send_bytes(404, "text/plain; charset=utf-8", b"not found")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be an object")
                    command = str(payload.get("command", ""))
                except (ValueError, json.JSONDecodeError):
                    self.send_bytes(400, "text/plain; charset=utf-8", b"bad request")
                    return
                if command == "select":
                    index = payload.get("index")
                    with preview.lock:
                        count = preview.count
                    if type(index) is not int or not 0 <= index < count:
                        self.send_bytes(400, "text/plain; charset=utf-8", b"bad index")
                        return
                    preview.commands.put((command, index))
                elif command in {"previous", "next", "reload", "close"}:
                    preview.commands.put(command)
                else:
                    self.send_bytes(400, "text/plain; charset=utf-8", b"bad command")
                    return
                self.send_bytes(204, "text/plain; charset=utf-8", b"")

            def log_message(self, _format: str, *_args: object) -> None:
                pass

        return Handler

    def start(self) -> None:
        identity = tailscale_identity()
        candidates = []
        if identity:
            candidates.append((*identity, "tailscale"))
        candidates.append(("127.0.0.1", "127.0.0.1", "localhost"))

        last_error = None
        for bind_host, display_host, network in candidates:
            try:
                self.server = ThreadingHTTPServer(
                    (bind_host, 0), self._handler()
                )
                self.server.daemon_threads = True
                port = self.server.server_address[1]
                root = f"/cpen/{self.token}/"
                self.url = f"http://{display_host}:{port}{root}"
                self.network = network
                if network == "localhost":
                    self.tunnel_command = (
                        f"ssh -N -L {port}:127.0.0.1:{port} <SSH_HOST>"
                    )
                self.thread = threading.Thread(
                    target=self.server.serve_forever,
                    name="cpen-browser-preview",
                    daemon=True,
                )
                self.thread.start()
                return
            except OSError as error:
                last_error = error
        raise RuntimeError(f"브라우저 preview 서버를 열지 못했습니다: {last_error}")

    def select(self, frame_name: str, position: int, frame_names: list[str]) -> None:
        with self.lock:
            self.frame_name = frame_name
            self.frame_names = list(frame_names)
            self.position = position
            self.count = len(frame_names)
            self.png = b""

    def update(self, data_base64: str) -> None:
        with self.lock:
            self.png = base64.b64decode(data_base64)
            self.revision += 1

    def poll_commands(self) -> list[str | tuple[str, int]]:
        commands = []
        while True:
            try:
                commands.append(self.commands.get_nowait())
            except queue.Empty:
                return commands

    def close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)


def compact_preview(columns: int, lines: int) -> bool:
    return columns < 40 or lines < 32


def clipped(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)] + "…"


def preview_access_text(url: str) -> str:
    link = f"\x1b]8;;{url}\x1b\\\x1b[4;36m미리보기 열기\x1b[0m\x1b]8;;\x1b\\"
    return f"{link}\n{url}"


def osc52_copy(text: str) -> str:
    encoded = base64.b64encode(text.encode()).decode()
    return f"\x1b]52;c;{encoded}\x07"


def preview(file_path: str, ready_path: str = "", source_pane: str = "") -> int:
    pane_id = os.environ.get("HERDR_PANE_ID", "")
    if not pane_id or not sys.stdin.isatty():
        print("cpen: Herdr pane에서 실행해야 합니다", file=sys.stderr)
        return 1

    ready_signaled = False
    if source_pane:
        print(f"Pencil preview 연결 중 · {Path(file_path).name}")
        if ready_path:
            Path(ready_path).touch()
            ready_signaled = True

    mcp = None
    for _ in range(12):
        try:
            mcp = PencilMCP()
            break
        except (OSError, RuntimeError, json.JSONDecodeError):
            time.sleep(0.5)
    if not mcp:
        print("cpen: Pencil 데스크톱에 연결하지 못했습니다. 앱 실행 상태를 확인하세요.")
        if source_pane:
            time.sleep(4)
            subprocess.run(
                [herdr_bin(), "pane", "close", pane_id], capture_output=True
            )
        return 1

    selected = 0
    frames: list[dict] = []
    web = PreviewWebServer(file_path)
    export_dir = tempfile.TemporaryDirectory(prefix="cpen-preview-")
    cache: PreviewImageCache | None = None
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    resized = True
    last_mtime = 0.0
    status_message = ""

    def on_resize(_signum: int, _frame: object) -> None:
        nonlocal resized
        resized = True

    def draw() -> None:
        nonlocal resized
        size = shutil.get_terminal_size((40, 20))
        compact = compact_preview(size.columns, size.lines)
        menu_rows = 3 if compact else min(max(len(frames) + 3, 5), 9)
        sys.stdout.write("\x1b[2J\x1b[H")
        title = f"Pencil browser preview · {Path(file_path).name}"
        sys.stdout.write(f"{clipped(title, size.columns)}\n")
        sys.stdout.write(f"{preview_access_text(web.url)}\n")
        if web.tunnel_command:
            sys.stdout.write(f"{web.tunnel_command}\n")
        sys.stdout.write(f"{'─' * max(1, size.columns - 1)}\n")
        if compact:
            name = frames[selected]["name"] if frames else ""
            sys.stdout.write(f"› {clipped(name, max(1, size.columns - 2))}\n")
            controls = "c copy  j/k frame  r reload  q close"
        else:
            visible = frames[
                max(0, selected - 2): max(0, selected - 2) + menu_rows - 3
            ]
            start = max(0, selected - 2)
            for offset, frame in enumerate(visible):
                marker = "›" if start + offset == selected else " "
                sys.stdout.write(f"{marker} {frame['name']}\n")
            controls = "c: URL 복사  ↑/↓ or j/k: frame  r: reload  q: close"
        if status_message:
            controls += f" · {status_message}"
        sys.stdout.write(clipped(controls, size.columns))
        sys.stdout.flush()
        resized = False

    def show_selected() -> None:
        nonlocal status_message
        if not frames or not cache:
            return
        frame = frames[selected]
        png = cache.select(frame)
        status_message = "" if png else "이미지 준비 중"
        web.select(
            frame["name"], selected + 1, [item["name"] for item in frames]
        )
        if png:
            web.update(png)
        draw()

    def request_reload() -> None:
        nonlocal status_message
        if not cache:
            return
        selected_id = frames[selected]["id"] if frames else ""
        status_message = "이미지 캐시 갱신 중"
        cache.reload(selected_id)
        draw()

    def handle_command(command: str | tuple[str, int]) -> bool:
        nonlocal selected, last_mtime
        if isinstance(command, tuple):
            if 0 <= command[1] < len(frames):
                selected = command[1]
                show_selected()
            return True
        if command == "close":
            return False
        if command == "next" and selected < len(frames) - 1:
            selected += 1
            show_selected()
        elif command == "previous" and selected > 0:
            selected -= 1
            show_selected()
        elif command == "reload":
            last_mtime = Path(file_path).stat().st_mtime
            request_reload()
        return True

    def process_cache_results() -> None:
        nonlocal frames, selected, status_message
        if not cache:
            return
        for result in cache.poll():
            if result["kind"] == "frames":
                new_frames = result["frames"]
                if not new_frames:
                    raise RuntimeError("미리볼 최상위 프레임이 없습니다")
                old_id = (
                    frames[selected]["id"] if frames else result["selected_id"]
                )
                frames = new_frames
                selected = next(
                    (i for i, item in enumerate(frames) if item["id"] == old_id),
                    0,
                )
                status_message = "이미지 준비 중"
                web.select(
                    frames[selected]["name"],
                    selected + 1,
                    [item["name"] for item in frames],
                )
                cache.warm(
                    frames, frames[selected]["id"], result["generation"]
                )
                draw()
            elif result["kind"] == "image":
                if frames and frames[selected]["id"] == result["frame_id"]:
                    status_message = ""
                    web.update(result["png"])
                    draw()
            else:
                status_message = f"이미지 갱신 실패: {result['message']}"
                draw()

    signal.signal(signal.SIGWINCH, on_resize)
    exit_code = 0
    try:
        web.start()
        frames = mcp.frames(file_path)
        if not frames:
            raise RuntimeError("미리볼 최상위 프레임이 없습니다")
        initial_png = mcp.export_png(
            file_path, frames[selected]["id"], export_dir.name
        )
        web.select(
            frames[selected]["name"],
            selected + 1,
            [item["name"] for item in frames],
        )
        web.update(initial_png)
        cache = PreviewImageCache(mcp, file_path, export_dir.name)
        cache.seed(frames[selected]["id"], initial_png)
        cache.warm(frames, frames[selected]["id"])
        last_mtime = Path(file_path).stat().st_mtime
        if ready_path and not ready_signaled:
            Path(ready_path).touch()
            ready_signaled = True
        tty.setcbreak(sys.stdin.fileno())
        draw()
        while True:
            process_cache_results()
            if any(not handle_command(command) for command in web.poll_commands()):
                break
            if resized:
                draw()
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready:
                key = os.read(sys.stdin.fileno(), 1)
                if key == b"\x1b" and select.select([sys.stdin], [], [], 0.03)[0]:
                    key += os.read(sys.stdin.fileno(), 2)
                if key in (b"c", b"C"):
                    sys.stdout.write(osc52_copy(web.url))
                    status_message = "복사 완료"
                    draw()
                    continue
                command = {
                    b"q": "close", b"Q": "close",
                    b"j": "next", b"\x1b[B": "next",
                    b"k": "previous", b"\x1b[A": "previous",
                    b"r": "reload", b"R": "reload",
                    b"\r": "reload", b"\n": "reload",
                }.get(key, "")
                if command and not handle_command(command):
                    break
            current_mtime = Path(file_path).stat().st_mtime
            if current_mtime != last_mtime:
                last_mtime = current_mtime
                request_reload()
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        sys.stdout.write(f"\x1b[2J\x1b[Hcpen preview 오류\n\n{error}\n")
        sys.stdout.flush()
        time.sleep(4)
        exit_code = 1
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        web.close()
        if cache:
            cache.close()
        else:
            mcp.close()
        export_dir.cleanup()
    if source_pane:
        subprocess.run(
            [herdr_bin(), "pane", "close", pane_id], capture_output=True
        )
    return exit_code


def event_payload() -> dict:
    try:
        return json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
    except json.JSONDecodeError:
        return {}


def event_pane_id(payload: dict) -> str:
    pane = payload.get("pane") or {}
    return str(payload.get("pane_id") or pane.get("pane_id") or "")


def handle_plugin_event() -> int:
    event_name = os.environ.get("HERDR_PLUGIN_EVENT", "")
    payload = event_payload()
    if event_name == "pane.closed":
        pane_id = event_pane_id(payload)
        if not pane_id:
            return 0
        source = read_binding(pane_id)
        if source:
            preview_id = str(source.get("preview_pane_id", ""))
            remove_binding(pane_id)
            if preview_id and preview_id != pane_id:
                subprocess.run(
                    [herdr_bin(), "pane", "close", preview_id],
                    capture_output=True,
                    check=False,
                )
        preview_source = binding_for_preview(pane_id)
        if preview_source:
            set_binding_preview(preview_source["pane_id"], "")
        return 0
    if event_name == "pane.moved":
        previous_pane_id = str(payload.get("previous_pane_id") or "")
        pane_id = event_pane_id(payload)
        if previous_pane_id and pane_id and previous_pane_id != pane_id:
            move_binding(previous_pane_id, pane_id)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    if sys.argv[1] == "open":
        return open_launcher()
    if sys.argv[1] == "launcher":
        return launch_session()
    if sys.argv[1] == "toggle-preview":
        return toggle_preview()
    if sys.argv[1] == "attach":
        focused_id, _workspace = focused_pane_values()
        return open_attach_launcher(focused_id) if focused_id else 1
    if sys.argv[1] == "attach-preview":
        return attach_preview()
    if sys.argv[1] == "detach":
        return detach_pane()
    if sys.argv[1] == "event":
        return handle_plugin_event()
    if sys.argv[1] == "bind" and len(sys.argv) == 4:
        write_binding(sys.argv[2], sys.argv[3])
        return 0
    if sys.argv[1] == "preview" and len(sys.argv) in (3, 4, 5):
        ready_path = sys.argv[3] if len(sys.argv) == 4 else ""
        if len(sys.argv) == 5:
            ready_path = sys.argv[3]
        source_pane = sys.argv[4] if len(sys.argv) == 5 else ""
        return preview(str(Path(sys.argv[2]).resolve()), ready_path, source_pane)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
