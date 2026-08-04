#!/usr/bin/env python3
"""Small Herdr launcher and read-only Pencil preview for cpen."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import tempfile
import time
import tty
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

        script = str(Path(__file__).resolve())
        ready_path = Path(tempfile.gettempdir()) / f"cpen-preview-{uuid.uuid4()}.ready"
        previous_bundle = frontmost_bundle_id()
        try:
            open_pen(pen_file)
            time.sleep(0.75)
            pane_run(
                right_id,
                [sys.executable, script, "preview", pen_file, str(ready_path)],
            )
            if not wait_for_ready(ready_path):
                raise RuntimeError("Pencil preview 연결 시간이 초과됐습니다")
        finally:
            activate_bundle(previous_bundle)
            ready_path.unlink(missing_ok=True)
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
                {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "cpen-preview", "version": "0.1.0"}},
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


def png_size(data_base64: str) -> tuple[int, int]:
    header = base64.b64decode(data_base64[:40])
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("유효한 PNG가 아닙니다")
    return struct.unpack(">II", header[16:24])


def fit_grid(
    image_width: int,
    image_height: int,
    max_cols: int,
    max_rows: int,
    cell_width: int,
    cell_height: int,
) -> tuple[int, int]:
    max_cols = min(max_cols, max(1, image_width // cell_width))
    max_rows = min(max_rows, max(1, image_height // cell_height))
    cols_at_max_height = round(
        max_rows * cell_height * image_width / image_height / cell_width
    )
    if cols_at_max_height <= max_cols:
        return max(1, cols_at_max_height), max_rows
    rows_at_max_width = round(
        max_cols * cell_width * image_height / image_width / cell_height
    )
    return max_cols, max(1, min(max_rows, rows_at_max_width))


def preview(file_path: str, ready_path: str = "") -> int:
    pane_id = os.environ.get("HERDR_PANE_ID", "")
    if not pane_id or not sys.stdin.isatty():
        print("cpen: Herdr pane에서 실행해야 합니다", file=sys.stderr)
        return 1

    mcp = None
    for _ in range(12):
        try:
            mcp = PencilMCP()
            break
        except (OSError, RuntimeError, json.JSONDecodeError):
            time.sleep(0.5)
    if not mcp:
        print("cpen: Pen.app MCP에 연결하지 못했습니다. Pen.app을 확인하세요.")
        return 1

    selected = 0
    frames: list[dict] = []
    current_png = ""
    export_dir = tempfile.TemporaryDirectory(prefix="cpen-preview-")
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    resized = True
    last_mtime = 0.0
    cell_width = 1
    cell_height = 1

    def on_resize(_signum: int, _frame: object) -> None:
        nonlocal resized
        resized = True

    def draw() -> None:
        nonlocal resized
        size = shutil.get_terminal_size((40, 20))
        menu_rows = min(max(len(frames) + 3, 5), 9)
        image_rows = max(1, size.lines - menu_rows)
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write(f"Pencil preview · {Path(file_path).name}\n")
        sys.stdout.write(f"\x1b[{image_rows + 1};1H")
        sys.stdout.write(f"{'─' * max(1, size.columns - 1)}\n")
        visible = frames[max(0, selected - 2): max(0, selected - 2) + menu_rows - 3]
        start = max(0, selected - 2)
        for offset, frame in enumerate(visible):
            marker = "›" if start + offset == selected else " "
            sys.stdout.write(f"{marker} {frame['name']}\n")
        sys.stdout.write("↑/↓ or j/k: frame  r: reload  q: close")
        sys.stdout.flush()
        if current_png:
            width, height = png_size(current_png)
            grid_cols, grid_rows = fit_grid(
                width,
                height,
                size.columns,
                max(1, image_rows - 1),
                cell_width,
                cell_height,
            )
            herdr_request(
                "pane.graphics.set",
                {"pane_id": pane_id, "format": "png", "image_width": width, "image_height": height,
                 "data_base64": current_png,
                 "placement": {
                     "viewport_col": max(0, (size.columns - grid_cols) // 2),
                     "viewport_row": 1,
                     "grid_cols": grid_cols,
                     "grid_rows": grid_rows,
                 }},
            )
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
        last_mtime = Path(file_path).stat().st_mtime

    signal.signal(signal.SIGWINCH, on_resize)
    try:
        try:
            graphics_info = herdr_request("pane.graphics.info", {"pane_id": pane_id})
            cell_width = graphics_info["cell_width_px"]
            cell_height = graphics_info["cell_height_px"]
        except RuntimeError as error:
            if "host cell size" in str(error):
                raise RuntimeError(
                    "이미지 미리보기는 Ghostty에서 Herdr를 실행해야 합니다 "
                    "(현재 iTerm2 클라이언트는 Kitty graphics 미지원)."
                ) from error
            raise
        reload_frames()
        if ready_path:
            Path(ready_path).touch()
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
                    draw()
                elif key in (b"k", b"\x1b[A") and selected > 0:
                    selected -= 1
                    current_png = mcp.export_png(
                        file_path, frames[selected]["id"], export_dir.name
                    )
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
        return 1
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        try:
            herdr_request("pane.graphics.clear", {"pane_id": pane_id})
        except Exception:
            pass
        mcp.close()
        export_dir.cleanup()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    if sys.argv[1] == "open":
        return open_launcher()
    if sys.argv[1] == "launcher":
        return launch_session()
    if sys.argv[1] == "preview" and len(sys.argv) in (3, 4):
        ready_path = sys.argv[3] if len(sys.argv) == 4 else ""
        return preview(str(Path(sys.argv[2]).resolve()), ready_path)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
