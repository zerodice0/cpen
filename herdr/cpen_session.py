#!/usr/bin/env python3
"""Small Herdr launcher and read-only Pencil preview for cpen."""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import select
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
PEN_MCP = Path(
    "/Applications/Pen.app/Contents/Resources/app.asar.unpacked/"
    "out/mcp-server-darwin-arm64"
)
CPEN_RUNNER = Path(__file__).with_name("run_cpen.fish")
FRAME_QUERY = (
    'Get(document,(n,c)=>c.depth===0 && n.type==="frame" && !n.reusable '
    '&& Print(JSON.stringify({id:n.id,name:n.name||"Untitled"})))'
)
PEN_FILE_PATTERN = re.compile(r"작업 대상 \.pen 파일: ([^\n]+)")
PREVIEW_HTML = Path(__file__).with_name("preview.html")


def context() -> dict:
    try:
        return json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}"))
    except json.JSONDecodeError:
        return {}


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


def agent_command(agent: str, pen_file: str, stem: str) -> list[str]:
    return [
        "fish",
        str(CPEN_RUNNER),
        "-a",
        agent,
        "--file",
        pen_file,
        f"pen:{stem}",
    ]


def pencil_mcp_command(binary: Path) -> list[str]:
    agent = f"cpenPreview-{uuid.uuid4().hex[:8]}"
    return [str(binary), "--app", "desktop", "--agent", agent]


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
    command = [
        "fd", "--no-ignore", "--absolute-path", "--type", "f", "--extension", "pen",
        "-E", "build", "-E", ".dart_tool", "-E", "node_modules", "-E", "Pods",
        "-E", ".git", "-E", "DerivedData", ".", root,
    ]
    paths = subprocess.run(command, text=True, capture_output=True, check=True).stdout.splitlines()
    return [str(Path(item).resolve()) for item in paths]


def open_pen(file_path: str) -> None:
    # The MCP transport binds a new agent to Pen's active document window.
    subprocess.run(
        ["/usr/bin/open", "-a", "Pen", file_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def frontmost_bundle_id() -> str:
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get bundle identifier of '
            "first application process whose frontmost is true",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def activate_bundle(bundle_id: str) -> None:
    if bundle_id:
        subprocess.run(
            ["/usr/bin/open", "-b", bundle_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


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
    previous_bundle = frontmost_bundle_id()
    try:
        open_pen(pen_file)
        time.sleep(0.75)
        command = [sys.executable, script, "preview", pen_file, str(ready_path)]
        if source_pane:
            command.append(source_pane)
        pane_run(pane_id, command)
        if not wait_for_ready(ready_path):
            raise RuntimeError("Pencil preview 연결 시간이 초과됐습니다")
    finally:
        activate_bundle(previous_bundle)
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


def toggle_preview() -> int:
    data = context()
    focused_id = str(
        data.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID") or ""
    )
    workspace = str(
        data.get("workspace_id") or os.environ.get("HERDR_WORKSPACE_ID") or ""
    )
    if not focused_id or not workspace:
        print("cpen: focused Herdr pane을 확인할 수 없습니다.", file=sys.stderr)
        return 1

    preview_id = ""
    try:
        focused_info = pane_process_info(focused_id)
        if preview_source_from_process_info(focused_info):
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
        pen_file = pen_file_from_pane(source_pane, focused_info)
        if not pen_file:
            raise RuntimeError("기존 cpen 에이전트 pane에서 실행하세요")

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
    rows = [f"{path}\t{os.path.relpath(path, cwd)}" for path in paths]
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
    tab_id = ""
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

        start_preview(right_id, pen_file)
        pane_run(left_id, agent_command(agent, pen_file, stem))
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
        if tab_id:
            subprocess.run([herdr_bin(), "tab", "close", tab_id], capture_output=True)
        time.sleep(3)
        return 1


class PencilMCP:
    def __init__(self) -> None:
        binary = Path(os.environ.get("CPEN_PENCIL_MCP", str(PEN_MCP)))
        self.process = subprocess.Popen(
            pencil_mcp_command(binary),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None if os.environ.get("CPEN_MCP_DEBUG") else subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self.next_id = 0
        try:
            self.request(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "cpen-preview", "version": "0.3.0"}},
            )
            self.notify("notifications/initialized", {})
            # The desktop socket assigns the client and agent name asynchronously.
            time.sleep(0.25)
        except Exception:
            self.close()
            raise

    def request(self, method: str, params: dict) -> dict:
        self.next_id += 1
        request_id = self.next_id
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        while True:
            if not select.select([self.process.stdout], [], [], 20)[0]:
                raise RuntimeError(f"Pen MCP 응답 시간 초과: {method}")
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("Pen MCP 연결이 종료되었습니다")
            message = json.loads(line)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(message["error"].get("message", str(message["error"])))
            return message["result"]

    def notify(self, method: str, params: dict) -> None:
        assert self.process.stdin
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self.process.stdin.flush()

    def call(self, name: str, arguments: dict) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            text = next((item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"), "Pencil MCP 오류")
            raise RuntimeError(text)
        return result

    def frames(self, file_path: str) -> list[dict]:
        result = self.call("execute", {"filePath": file_path, "input": FRAME_QUERY})
        text = "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")
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
        directory = Path(output_dir)
        for old_file in directory.glob("*.png"):
            old_file.unlink()
        try:
            self.call(
                "export_nodes",
                {
                    "filePath": file_path,
                    "nodeIds": [node_id],
                    "outputDir": output_dir,
                    "format": "png",
                    "scale": 2,
                },
            )
            exported = list(directory.glob("*.png"))
            if len(exported) != 1:
                raise RuntimeError("Pencil MCP가 PNG 파일 하나를 내보내지 않았습니다")
            return base64.b64encode(exported[0].read_bytes()).decode()
        finally:
            for exported_file in directory.glob("*.png"):
                exported_file.unlink(missing_ok=True)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


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
        self.png = b""
        self.revision = 0
        self.token = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.server = None
        self.thread = None
        self.url = ""
        self.network = ""
        self.tunnel_command = ""

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

    def update(self, data_base64: str, frame_name: str) -> None:
        with self.lock:
            self.png = base64.b64decode(data_base64)
            self.frame_name = frame_name
            self.revision += 1

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
        print("cpen: Pen.app MCP에 연결하지 못했습니다. Pen.app을 확인하세요.")
        if source_pane:
            time.sleep(4)
            subprocess.run(
                [herdr_bin(), "pane", "close", pane_id], capture_output=True
            )
        return 1

    selected = 0
    frames: list[dict] = []
    current_png = ""
    web = PreviewWebServer(file_path)
    export_dir = tempfile.TemporaryDirectory(prefix="cpen-preview-")
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    resized = True
    last_mtime = 0.0

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
            sys.stdout.write("j/k frame  r reload  q close")
        else:
            visible = frames[
                max(0, selected - 2): max(0, selected - 2) + menu_rows - 3
            ]
            start = max(0, selected - 2)
            for offset, frame in enumerate(visible):
                marker = "›" if start + offset == selected else " "
                sys.stdout.write(f"{marker} {frame['name']}\n")
            sys.stdout.write("↑/↓ or j/k: frame  r: reload  q: close")
        sys.stdout.flush()
        resized = False

    def reload_frames() -> None:
        nonlocal frames, selected, current_png, last_mtime
        old_id = frames[selected]["id"] if frames else ""
        frames = mcp.frames(file_path)
        if not frames:
            raise RuntimeError("미리볼 최상위 프레임이 없습니다")
        selected = next((i for i, item in enumerate(frames) if item["id"] == old_id), 0)
        current_png = mcp.export_png(
            file_path, frames[selected]["id"], export_dir.name
        )
        web.update(current_png, frames[selected]["name"])
        last_mtime = Path(file_path).stat().st_mtime

    signal.signal(signal.SIGWINCH, on_resize)
    exit_code = 0
    try:
        web.start()
        reload_frames()
        if ready_path and not ready_signaled:
            Path(ready_path).touch()
            ready_signaled = True
        tty.setcbreak(sys.stdin.fileno())
        draw()
        while True:
            if resized:
                draw()
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
            if ready:
                key = os.read(sys.stdin.fileno(), 1)
                if key == b"\x1b" and select.select([sys.stdin], [], [], 0.03)[0]:
                    key += os.read(sys.stdin.fileno(), 2)
                if key in (b"q", b"Q"):
                    break
                if key in (b"j", b"\x1b[B") and selected < len(frames) - 1:
                    selected += 1
                    current_png = mcp.export_png(
                        file_path, frames[selected]["id"], export_dir.name
                    )
                    web.update(current_png, frames[selected]["name"])
                    draw()
                elif key in (b"k", b"\x1b[A") and selected > 0:
                    selected -= 1
                    current_png = mcp.export_png(
                        file_path, frames[selected]["id"], export_dir.name
                    )
                    web.update(current_png, frames[selected]["name"])
                    draw()
                elif key in (b"r", b"R", b"\r", b"\n"):
                    reload_frames()
                    draw()
            elif Path(file_path).stat().st_mtime != last_mtime:
                reload_frames()
                draw()
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        sys.stdout.write(f"\x1b[2J\x1b[Hcpen preview 오류\n\n{error}\n")
        sys.stdout.flush()
        time.sleep(4)
        exit_code = 1
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        web.close()
        mcp.close()
        export_dir.cleanup()
    if source_pane:
        subprocess.run(
            [herdr_bin(), "pane", "close", pane_id], capture_output=True
        )
    return exit_code


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    if sys.argv[1] == "open":
        return open_launcher()
    if sys.argv[1] == "launcher":
        return launch_session()
    if sys.argv[1] == "toggle-preview":
        return toggle_preview()
    if sys.argv[1] == "preview" and len(sys.argv) in (3, 4, 5):
        ready_path = sys.argv[3] if len(sys.argv) == 4 else ""
        if len(sys.argv) == 5:
            ready_path = sys.argv[3]
        source_pane = sys.argv[4] if len(sys.argv) == 5 else ""
        return preview(str(Path(sys.argv[2]).resolve()), ready_path, source_pane)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
