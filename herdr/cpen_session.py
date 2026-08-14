#!/usr/bin/env python3
"""Small Herdr launcher for cpen Pencil sessions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid


PLUGIN_ID = "zerodice0.cpen"
PLUGIN_VERSION = "0.7.0"
CPEN_RUNNER = Path(__file__).resolve().parents[1] / "bin" / "cpen"
FRAME_QUERY = (
    'Get(document,(n,c)=>c.depth===0 && n.type==="frame" && !n.reusable '
    '&& Print(JSON.stringify({id:n.id,name:n.name||"Untitled"})))'
)
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


def write_binding(pane_id: str, pen_file: str) -> dict:
    current = read_binding(pane_id) or {}
    binding = {
        "version": BINDING_VERSION,
        "pane_id": pane_id,
        "pen_file": str(Path(pen_file).resolve()),
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


def move_binding(previous_pane_id: str, pane_id: str) -> None:
    source = read_binding(previous_pane_id)
    if source:
        write_binding(pane_id, source["pen_file"])
        remove_binding(previous_pane_id)


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


def pencil_socket_path() -> Path:
    configured = os.environ.get("CPEN_PENCIL_SOCKET")
    return Path(configured).expanduser() if configured else (
        Path.home() / ".pencil/socket/pencil-desktop.sock"
    )


def pencil_open_command(pen_file: str | Path) -> list[str]:
    override = os.environ.get("CPEN_PENCIL_APP")
    if override:
        return [override, str(Path(pen_file).resolve())]
    launcher = next(
        (
            path
            for name in ("pen-desktop", "xdg-open", "open")
            if (path := shutil.which(name))
        ),
        None,
    )
    return [launcher, str(Path(pen_file).resolve())] if launcher else []


def wait_for_pencil_desktop(pen_file: str, timeout: float = 22) -> None:
    target = str(Path(pen_file).resolve())
    deadline = time.monotonic() + timeout
    last_error = "desktop socket에 연결할 수 없습니다"
    while True:
        mcp = None
        try:
            mcp = PencilMCP()
            # A socket can exist before the selected document has finished loading.
            # Query the exact file so agent startup cannot race that load.
            mcp.frames(target)
            return
        except (OSError, RuntimeError, json.JSONDecodeError) as error:
            last_error = str(error)
        finally:
            if mcp is not None:
                mcp.close()
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Pencil desktop과 선택한 문서가 준비되지 않았습니다 "
                f"({pencil_socket_path()}): {last_error}"
            )
        time.sleep(0.1)


def prepare_pencil_desktop(pen_file: str) -> None:
    target = str(Path(pen_file).resolve())
    command = pencil_open_command(target)
    if not command:
        raise RuntimeError(
            "Pencil desktop launcher를 찾지 못했습니다 "
            "(CPEN_PENCIL_APP, pen-desktop, xdg-open 또는 open)"
        )
    try:
        result = subprocess.run(command, text=True, capture_output=True)
    except OSError as error:
        raise RuntimeError(f"Pencil desktop으로 파일을 열지 못했습니다: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"Pencil desktop으로 파일을 열지 못했습니다: {detail}")
    wait_for_pencil_desktop(target)


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
        prepare_pencil_desktop(pen_file)
        created = run_json(
            "tab", "create", "--workspace", workspace, "--cwd", cwd,
            "--label", label, "--no-focus",
        )["result"]
        tab_id = created["tab"]["tab_id"]
        left_id = created["root_pane"]["pane_id"]
        write_binding(left_id, pen_file)
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
        path = str(pencil_socket_path())
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
                        "agent": f"cpenReady-{uuid.uuid4().hex[:8]}",
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

    def close(self) -> None:
        self.socket.close()


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
        if read_binding(pane_id):
            remove_binding(pane_id)
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
    if sys.argv[1] == "event":
        return handle_plugin_event()
    if sys.argv[1] == "bind" and len(sys.argv) == 4:
        write_binding(sys.argv[2], sys.argv[3])
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
