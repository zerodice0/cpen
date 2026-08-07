#!/usr/bin/env python3
"""Fish-independent command line implementation for cpen."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid


SKIP_DIRS = {"build", ".dart_tool", "node_modules", "Pods", ".git", "DerivedData"}
LEGACY_HOOK_MARKS = ("cpen-guard", "cpen-focus")
PEN_APP_MCP = "/Applications/Pen.app/Contents/Resources/app.asar.unpacked/out/mcp-server-darwin-arm64"


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def git_root(path: Path) -> Path | None:
    command = ["git"]
    if path.is_file():
        path = path.parent
    command.extend(["-C", str(path), "rev-parse", "--show-toplevel"])
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode == 0 and result.stdout.strip():
        return resolved(result.stdout.strip())
    return None


def lease_dir() -> Path:
    explicit = os.environ.get("CPEN_LEASE_DIR")
    if explicit:
        return resolved(explicit)
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "cpen" / "leases"


def lease_cell(pen_file: str | Path) -> Path:
    key = hashlib.sha256(str(resolved(pen_file)).encode()).hexdigest()[:16]
    return lease_dir() / f"{key}.d"


def read_lease(cell: Path) -> list[str] | None:
    try:
        fields = (cell / "info").read_text().rstrip("\n").split("\t")
    except OSError:
        return None
    return fields if len(fields) >= 5 else None


def lease_alive(cell: Path) -> bool:
    fields = read_lease(cell)
    if not fields or not fields[1].isdigit():
        return False
    result = subprocess.run(
        ["ps", "-p", fields[1], "-o", "comm="], text=True, capture_output=True
    )
    if result.returncode != 0:
        return False
    name = Path(result.stdout.strip()).name.lower()
    # "fish" is accepted only to honor a lease owned by an already-running legacy
    # cpen session during upgrade. No Fish process is started by this implementation.
    return bool(re.search(r"^(cpen|codex|claude|fish|python(?:3(?:\.\d+)?)?)$", name))


def acquire_lease(
    pen_file: Path, agent: str, session: str, token: str, owner_pid: int
) -> int:
    root = lease_dir()
    cell = lease_cell(pen_file)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 2
    try:
        cell.mkdir()
    except FileExistsError:
        if lease_alive(cell):
            return 1
        shutil.rmtree(cell, ignore_errors=True)
        try:
            cell.mkdir()
        except FileExistsError:
            return 1
    try:
        timestamp = datetime.now().isoformat(timespec="seconds")
        (cell / "info").write_text(
            "\t".join(
                [str(resolved(pen_file)), str(owner_pid), agent, session, token, timestamp]
            )
            + "\n"
        )
    except OSError:
        shutil.rmtree(cell, ignore_errors=True)
        return 2
    return 0


def release_lease(pen_file: Path, token: str) -> int:
    cell = lease_cell(pen_file)
    if not cell.is_dir():
        return 0
    fields = read_lease(cell)
    if fields and fields[4] != token:
        return 1
    shutil.rmtree(cell, ignore_errors=True)
    return 0


def leases() -> list[list[str]]:
    root = lease_dir()
    if not root.is_dir():
        return []
    result = []
    for cell in root.glob("*.d"):
        fields = read_lease(cell)
        if fields and lease_alive(cell):
            result.append(fields)
    return result


def process_sessions() -> list[list[str]]:
    user = os.environ.get("USER", "")
    command = ["ps", "-U", user, "-wwo", "pid=,command="] if user else ["ps", "-ww", "-o", "pid=,command="]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        return []
    sessions = []
    for line in result.stdout.splitlines():
        if ".pen" not in line:
            continue
        head = re.match(r"\s*(\d+)\s+(?:\S*/)?(codex|claude)(?:\s|$)", line)
        pen = re.search(r"/[^ ]+\.pen", line)
        if head and pen:
            sessions.append([head.group(1), head.group(2), str(resolved(pen.group(0)))])
    return sessions


def occupants() -> list[list[str]]:
    result = [[row[0], row[1], row[2], row[3]] for row in leases()]
    seen = {(row[0], row[1]) for row in result}
    for pid, agent, pen_file in process_sessions():
        if (pen_file, pid) not in seen:
            result.append([pen_file, pid, agent, ""])
    return result


def busy_labels(pen_file: Path, rows: list[list[str]]) -> tuple[str, str]:
    matches = []
    target = str(resolved(pen_file))
    for path, pid, agent, session in rows:
        if path != target:
            continue
        matches.append(f'{agent}(pid {pid}, "{session}")' if session else f"{agent}(pid {pid})")
    if not matches:
        return "", ""
    if len(matches) == 1:
        return matches[0], matches[0]
    return f"세션 {len(matches)}개", f"세션 {len(matches)}개 ({', '.join(matches)})"


def find_pen_files(workdir: Path) -> list[Path]:
    files = []
    for root, dirs, names in os.walk(workdir):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        files.extend(resolved(Path(root) / name) for name in names if name.endswith(".pen"))
    return sorted(files)


def choose(lines: list[str], prompt: str, header: str) -> str | None:
    if not shutil.which("fzf"):
        eprint("cpen: 대화형 선택에는 fzf가 필요합니다")
        return None
    result = subprocess.run(
        ["fzf", "--prompt", prompt, "--height=40%", "--reverse", "--delimiter=\t", "--with-nth=2..", "--header", header],
        input="\n".join(lines) + "\n",
        text=True,
        capture_output=True,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def choose_agent() -> str | None:
    if not shutil.which("fzf"):
        eprint("cpen: 에이전트를 고르려면 fzf가 필요합니다. -a 옵션을 사용할 수도 있습니다")
        return None
    result = subprocess.run(
        ["fzf", "--prompt=Agent> ", "--height=~40%", "--reverse", "--header", "고정하려면 CPEN_AGENT=<agent> 또는 cpen -a <agent>"],
        input="codex\nclaude\n",
        text=True,
        capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def pencil_open_command(pen_file: Path) -> list[str]:
    override = os.environ.get("CPEN_PENCIL_APP")
    if override:
        return [override, str(pen_file)]
    launcher = next(
        (path for name in ("pen-desktop", "xdg-open", "open") if (path := shutil.which(name))),
        None,
    )
    return [launcher, str(pen_file)] if launcher else []


def open_pencil(pen_file: Path) -> bool:
    command = pencil_open_command(pen_file)
    if not command:
        return False
    try:
        return subprocess.run(command).returncode == 0
    except OSError:
        return False


def binding_script() -> Path | None:
    checkout = Path(__file__).resolve().parents[1] / "herdr" / "cpen_session.py"
    if checkout.is_file():
        return checkout
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    root = config / "herdr" / "plugins" / "github"
    matches = sorted(root.glob("*/zerodice0.cpen-*/herdr/cpen_session.py"))
    return matches[0] if matches else None


def bind_herdr_pane(pen_file: Path) -> None:
    pane_id = os.environ.get("HERDR_PANE_ID")
    script = binding_script()
    if not pane_id or not script:
        return
    subprocess.run(
        [sys.executable, str(script), "bind", pane_id, str(pen_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def validate_codex_pencil_mcp() -> bool:
    try:
        result = subprocess.run(
            ["codex", "mcp", "get", "pencil", "--json"],
            text=True,
            capture_output=True,
        )
    except OSError:
        eprint("cpen: Codex Pencil MCP 설정을 확인할 수 없습니다")
        return False
    if result.returncode != 0:
        eprint("cpen: Codex에 Pencil MCP가 등록되어 있지 않습니다")
        return False
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError:
        eprint("cpen: Codex Pencil MCP 설정을 읽을 수 없습니다")
        return False

    transport = document.get("transport", {})
    command = transport.get("command", "")
    args = transport.get("args", [])
    values = [command, *args] if isinstance(command, str) and isinstance(args, list) else []
    if not values or any("cursor" in str(value).lower() for value in values):
        eprint("cpen: Cursor Pencil MCP는 사용할 수 없습니다. Pen desktop MCP를 등록하세요")
        return False
    try:
        app = args[args.index("--app") + 1]
    except (ValueError, IndexError):
        app = ""
    try:
        agent = args[args.index("--agent") + 1]
    except (ValueError, IndexError):
        agent = ""
    if (
        transport.get("type") != "stdio"
        or app != "desktop"
        or agent != "codexCLI"
        or document.get("enabled") is False
    ):
        eprint("cpen: Codex Pencil MCP는 Pen desktop 대상으로 등록되어야 합니다")
        return False
    if sys.platform == "darwin" and command != PEN_APP_MCP:
        eprint(f"cpen: Codex Pencil MCP는 Pen.app 내장 서버를 사용해야 합니다: {PEN_APP_MCP}")
        return False
    return True


def build_prompt(
    session: str, pen_file: Path, concurrent: list[str], agent: str
) -> str:
    lines = [
        f"Pencil 작업 세션: {session}",
        f"작업 대상 .pen 파일: {pen_file}",
        "Pencil MCP 도구를 호출할 때 filePath 에는 항상 위 절대 경로를 넘기세요.",
        "수정은 위 파일을 중심으로 하되, 다른 세션도 같은 문서를 변경할 수 있다고 가정하세요.",
        "활성 캔버스(get_app_state)는 Pencil 앱 전역 공유라 다른 에이전트 세션 때문에 위 경로와 다를 수 있습니다. 그것을 이유로 멈추지 말고 filePath 로 작업하세요.",
        ".pen 파일은 Pencil MCP로만 읽고 수정하세요.",
        *concurrent,
    ]
    if agent == "codex":
        lines.append(f"첫 응답 마지막에 '세션 이름 지정: /rename {session}' 을 안내하세요.")
    return "\n".join(lines)


def run_agent(agent: str, workdir: Path, pen_file: Path, session: str, prompt: str) -> int:
    environment = os.environ.copy()
    environment.update({"CPEN_PEN_FILE": str(pen_file), "CPEN_SESSION": session})
    command = [agent]
    if agent == "codex":
        command.extend(["-C", str(workdir), prompt])
    else:
        command.extend(["--name", session, prompt])
    try:
        return subprocess.run(command, cwd=workdir, env=environment).returncode
    except FileNotFoundError:
        eprint(f"cpen: {agent} CLI를 찾을 수 없습니다")
        return 127
    except KeyboardInterrupt:
        return 130


def cpen_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpen",
        description=".pen 파일을 골라 Pencil 데스크톱 앱으로 열고 에이전트를 띄운다.",
    )
    parser.add_argument("-a", "--agent", choices=("codex", "claude"))
    parser.add_argument("-f", "--file")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install-hooks", action="store_true")
    group.add_argument("--uninstall-hooks", action="store_true")
    parser.add_argument("session", nargs="*", metavar="세션-이름")
    return parser


def main_cpen(argv: list[str] | None = None) -> int:
    args = cpen_parser().parse_args(argv)
    if args.install_hooks:
        if not (shutil.which("pen") or shutil.which("pencil")):
            eprint("cpen: 자동 저장 훅에는 pen 또는 pencil CLI가 필요합니다")
            return 1
        return edit_hooks("install")
    if args.uninstall_hooks:
        return edit_hooks("uninstall")

    agent = args.agent or os.environ.get("CPEN_AGENT") or choose_agent()
    if agent not in ("codex", "claude"):
        if agent:
            eprint(f"cpen: 알 수 없는 에이전트 '{agent}' (codex|claude)")
        return 1

    workdir = git_root(Path.cwd()) or Path.cwd().resolve()
    if args.file:
        pen_file = resolved(args.file)
        if not pen_file.is_file():
            eprint(f"cpen: .pen 파일을 찾을 수 없습니다: {pen_file}")
            return 1
        if pen_file.suffix != ".pen":
            eprint(f"cpen: .pen 파일이 아닙니다: {pen_file}")
            return 1
        workdir = git_root(pen_file) or workdir
    else:
        rows = occupants()
        files = find_pen_files(workdir)
        if not files:
            eprint(f"cpen: .pen 파일이 없습니다: {workdir}")
            return 1
        choices = []
        for path in files:
            display = str(path.relative_to(workdir))
            short, _ = busy_labels(path, rows)
            if short:
                display += f"  ⚠ 작업 중: {short}"
            choices.append(f"{path}\t{display}")
        picked = choose(
            choices,
            f"Pencil file ({agent})> ",
            "⚠ 는 다른 세션이 작업 중입니다 - 차단하지 않고 동시 작업 안내를 전달합니다",
        )
        if not picked:
            return 1
        pen_file = Path(picked.split("\t", 1)[0])

    if agent == "codex" and not validate_codex_pencil_mcp():
        return 1

    session = " ".join(args.session) or f"pen:{pen_file.stem}"
    token = uuid.uuid4().hex
    lease_status = acquire_lease(pen_file, agent, session, token, os.getpid())
    if lease_status == 2:
        eprint(f"cpen: 리스 디렉토리를 만들 수 없습니다: {lease_dir()}")
        return 1
    holds_lease = lease_status == 0
    concurrent = []
    if lease_status == 1:
        _, long_label = busy_labels(pen_file, occupants())
        long_label = long_label or "다른 세션"
        eprint(f"cpen: {pen_file.name} 을 {long_label} 가 작업 중입니다 - 동시 작업으로 시작합니다.")
        concurrent.extend(
            [
                f"현재 같은 파일을 {long_label} 가 작업 중입니다. 필요한 경우 같은 파일에 수정할 수 있지만, 다른 세션의 변경을 덮어쓰거나 되돌리지 마세요.",
                "수정 직전에 대상 노드를 다시 읽고, 기억한 상태와 다르면 최신 상태를 기준으로 작업하세요.",
                "한 번의 execute 범위를 작게 유지하고, 충돌이 의심되면 재시도보다 사용자에게 현재 상태를 보고하세요.",
            ]
        )
    external = os.environ.get("CPEN_EXTERNAL_OCCUPANTS", "")
    if external:
        eprint(f"cpen: {pen_file} 에 연결된 Herdr pane이 있습니다: {external}")
        concurrent.append(
            f"현재 같은 파일에 연결된 Herdr pane이 있습니다: {external}. 다른 세션의 변경을 덮어쓰거나 되돌리지 마세요."
        )
        if lease_status != 1:
            concurrent.extend(
                [
                    "수정 직전에 대상 노드를 다시 읽고, 기억한 상태와 다르면 최신 상태를 기준으로 작업하세요.",
                    "한 번의 execute 범위를 작게 유지하고, 충돌이 의심되면 재시도보다 사용자에게 현재 상태를 보고하세요.",
                ]
            )

    try:
        if not os.environ.get("CPEN_SKIP_OPEN") and not open_pencil(pen_file):
            eprint(f"cpen: Pencil 데스크톱 앱으로 파일을 열지 못했습니다: {pen_file}")
            return 1
        bind_herdr_pane(pen_file)
        prompt = build_prompt(session, pen_file, concurrent, agent)
        return run_agent(agent, workdir, pen_file, session, prompt)
    finally:
        if holds_lease:
            release_lease(pen_file, token)


def focus_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cpen-focus")
    parser.add_argument("--if-touched", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("file", nargs="?")
    return parser


def main_focus(argv: list[str] | None = None) -> int:
    args = focus_parser().parse_args(argv)
    if args.if_touched:
        sys.stdin.read()
        return 0
    file_value = args.file or os.environ.get("CPEN_PEN_FILE")
    if not file_value:
        eprint("cpen-focus: 대상 .pen 이 없습니다 - 경로를 넘기거나 cpen 세션 안에서 실행하세요")
        return 1
    pen_file = resolved(file_value)
    if not open_pencil(pen_file):
        eprint(f"cpen-focus: Pencil 데스크톱 앱으로 열지 못했습니다: {pen_file}")
        return 1
    return 0


def save_file(pen_file: Path, hook: bool) -> int:
    pencil = shutil.which("pen") or shutil.which("pencil")
    if not pencil:
        eprint("cpen-save: pen 또는 pencil CLI를 찾을 수 없습니다")
        return 1
    result = subprocess.run(
        [pencil, "interactive", "--app", "desktop", "--in", str(pen_file)],
        input="save()\nexit()\n",
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return 1
    print("{}" if hook else f"cpen: 저장 완료 ({pen_file})")
    return 0


def save_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cpen-save")
    parser.add_argument("--hook", action="store_true")
    parser.add_argument("file", nargs="?")
    return parser


def main_save(argv: list[str] | None = None) -> int:
    args = save_parser().parse_args(argv)
    file_value = args.file or os.environ.get("CPEN_PEN_FILE")
    if not file_value:
        if args.hook:
            print("{}")
            return 0
        eprint("cpen-save: 저장할 .pen 파일이 없습니다")
        return 1
    pen_file = resolved(file_value)
    if not pen_file.is_file():
        eprint(f"cpen-save: 파일을 찾을 수 없습니다: {pen_file}")
        return 1
    if pen_file.suffix != ".pen":
        eprint(f"cpen-save: .pen 파일만 저장할 수 있습니다: {pen_file}")
        return 1
    return save_file(pen_file, args.hook)


def hook_command() -> str:
    entrypoint = Path(__file__).with_name("cpen-save").resolve()
    return shlex.join([sys.executable, str(entrypoint), "--hook"])


def hook_commands(document: dict) -> list[str]:
    return [
        handler.get("command", "")
        for groups in (document.get("hooks") or {}).values()
        for group in groups or []
        for handler in group.get("hooks") or []
    ]


def update_hook_file(mode: str, path: Path, save_command: str) -> tuple[int, str]:
    try:
        document = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as error:
        return 2, f"설정을 읽지 못했습니다: {error}"
    hooks = document.get("hooks") or {}

    if mode == "status":
        return (0, "") if any("cpen-save" in command for command in hook_commands(document)) else (1, "")

    changed = []
    marks = ("cpen-save", *LEGACY_HOOK_MARKS)
    for event in list(hooks):
        kept_groups = []
        for group in hooks.get(event) or []:
            if "hooks" not in group:
                kept_groups.append(group)
                continue
            kept = []
            for handler in group.get("hooks") or []:
                command = handler.get("command", "")
                hit = next((mark for mark in marks if mark in command), None)
                if hit:
                    changed.append(f"{hit}(제거)" if mode == "install" else hit)
                else:
                    kept.append(handler)
            if kept:
                group["hooks"] = kept
                kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)

    if mode == "install":
        groups = hooks.setdefault("Stop", [])
        groups.append({"hooks": [{"type": "command", "command": save_command}]})
        changed.append("cpen-save")
    elif mode != "uninstall":
        return 2, f"알 수 없는 모드: {mode}"

    if hooks:
        document["hooks"] = hooks
    else:
        document.pop("hooks", None)
    if not changed:
        return 0, "설치되어 있지 않음"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = Path(f"{path}.cpen-backup")
        if path.exists() and not backup.exists():
            shutil.copyfile(path, backup)
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "w") as output:
            json.dump(document, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    except OSError as error:
        return 2, f"설정을 쓰지 못했습니다: {error}"
    return 0, f"완료: {', '.join(dict.fromkeys(changed))}"


def edit_hooks(mode: str) -> int:
    home = Path.home()
    targets = []
    if shutil.which("claude"):
        targets.append(("claude", home / ".claude" / "settings.json"))
    if shutil.which("codex"):
        targets.append(("codex", home / ".codex" / "hooks.json"))
    if not targets:
        if mode != "status":
            eprint("cpen: codex 도 claude 도 찾을 수 없어 설정할 훅이 없습니다")
        return 1
    failed = 0
    command = hook_command()
    for label, path in targets:
        status, message = update_hook_file(mode, path, command)
        if status:
            failed = 1
            if mode != "status":
                eprint(f"cpen: {label} 훅 설정 실패 ({path}): {message}")
        elif mode != "status":
            print(f"cpen: {label} {message} ({path})")
    if mode == "install" and shutil.which("codex"):
        eprint("cpen: codex 새 세션에서 /hooks 를 열어 새 cpen 훅을 승인하세요.")
    return failed


def main_guard(argv: list[str] | None = None) -> int:
    del argv
    sys.stdin.read()
    return 0
