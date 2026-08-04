function _cpen_hooks --description "자동 저장 훅 설치/제거/조회"
    set -l mode $argv[1]
    if not contains -- $mode install uninstall status
        echo "_cpen_hooks: 지원하는 명령은 install, uninstall, status 입니다" >&2
        return 1
    end

    set -l files
    command -q claude; and set -a files claude "$HOME/.claude/settings.json"
    command -q codex; and set -a files codex "$HOME/.codex/hooks.json"

    if test (count $files) -eq 0
        test "$mode" = status; and return 1
        echo "cpen: codex 도 claude 도 찾을 수 없어 설정할 훅이 없습니다" >&2
        return 1
    end

    set -l failed 0
    for i in (seq 1 2 (count $files))
        set -l label $files[$i]
        set -l file $files[(math $i + 1)]
        set -l out (_cpen_hooks_edit $mode $file)
        if test $status -ne 0
            test "$mode" = status; or echo "cpen: $label 훅 설정 실패 ($file): $out" >&2
            set failed 1
        else if test "$mode" != status
            echo "cpen: $label $out ($file)"
        end
    end

    if test "$mode" = install; and command -q codex
        echo "cpen: codex 새 세션에서 /hooks 를 열어 새 cpen 훅을 승인하세요." >&2
    end
    return $failed
end

function _cpen_hooks_edit --description "설정 파일 하나에 대해 자동 저장 훅을 설치/제거/조회"
    set -l mode $argv[1]
    set -l file $argv[2]
    set -l save_cmd 'fish -c "cpen-save --hook"'

    set -l out (python3 -c '
import json, os, sys, tempfile

mode, path, save_cmd = sys.argv[1:4]
SAVE = {"event": "Stop", "mark": "cpen-save", "command": save_cmd}
LEGACY_MARKS = ("cpen-guard", "cpen-focus")

if os.path.exists(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except Exception as e:
        print("설정을 읽지 못했습니다: %s" % e)
        sys.exit(2)
else:
    doc = {}

hooks = doc.get("hooks") or {}

def groups_of(event):
    return hooks.get(event) or []

def matching(mark):
    return [h for g in groups_of(SAVE["event"])
            for h in (g.get("hooks") or [])
            if mark in (h.get("command") or "")]

def remove_marks(marks):
    removed = []
    for event in list(hooks):
        kept_groups = []
        for group in hooks.get(event) or []:
            old = group.get("hooks")
            if old is None:
                kept_groups.append(group)
                continue
            kept = []
            for handler in old:
                command = handler.get("command") or ""
                hit = next((mark for mark in marks if mark in command), None)
                if hit:
                    removed.append(hit)
                else:
                    kept.append(handler)
            if kept:
                group["hooks"] = kept
                kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return removed

if mode == "status":
    sys.exit(0 if matching(SAVE["mark"]) else 1)

changed = []
if mode == "install":
    for mark in dict.fromkeys(remove_marks(LEGACY_MARKS)):
        changed.append(mark + "(제거)")

    installed = matching(SAVE["mark"])
    if installed:
        stale = [h for h in installed if h.get("command") != SAVE["command"]]
        for handler in stale:
            handler["command"] = SAVE["command"]
        if stale:
            changed.append(SAVE["mark"] + "(갱신)")
    else:
        groups = groups_of(SAVE["event"])
        groups.append({
            "hooks": [{"type": "command", "command": SAVE["command"]}],
        })
        hooks[SAVE["event"]] = groups
        changed.append(SAVE["mark"])
elif mode == "uninstall":
    for mark in dict.fromkeys(remove_marks((SAVE["mark"],) + LEGACY_MARKS)):
        changed.append(mark)
else:
    print("알 수 없는 모드: %s" % mode)
    sys.exit(2)

if not changed:
    print("이미 설치됨" if mode == "install" else "설치되어 있지 않음")
    sys.exit(0)

if hooks:
    doc["hooks"] = hooks
else:
    doc.pop("hooks", None)

backup = path + ".cpen-backup"
if os.path.exists(path) and not os.path.exists(backup):
    with open(path) as src, open(backup, "w") as dst:
        dst.write(src.read())

os.makedirs(os.path.dirname(path), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    json.dump(doc, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("완료: %s" % ", ".join(changed))
' $mode $file $save_cmd)
    set -l rc $status

    if test "$mode" = status
        return $rc
    end
    if test $rc -ne 0
        echo $out
        return $rc
    end
    echo $out
end
