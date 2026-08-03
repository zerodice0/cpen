function _cpen_hooks --description "PreToolUse 가드 훅 설치/제거/조회"
    # 훅을 *전역* 설정에만 넣는다. 프로젝트 훅(.codex/hooks.json 등)은 신뢰 승인을
    # 받아야 하고 headless 실행에서는 조용히 무시되어, 있는 줄 알았는데 안 걸리는
    # 최악의 실패로 이어진다.
    set -l mode $argv[1]

    # 훅은 stdin 으로 payload 를 받는 명령이다. 함수는 autoload 되므로
    # 파일 경로 대신 fish -c 로 부른다 - fisher 로 설치해도 경로가 안 깨진다.
    set -l cmd "fish -c cpen-guard"

    set -l ok 0
    set -l checked 0

    # claude 는 mcp__pencil__execute, codex 는 pencil/execute 로 도구를 부른다.
    # 둘 다 걸리도록 느슨하게 잡고, 진짜 판정은 훅 안에서 filePath 로 한다.
    set -l matcher '.*[Pp]encil.*'

    if command -q claude
        set checked (math $checked + 1)
        _cpen_hooks_edit $mode $HOME/.claude/settings.json $matcher $cmd claude
        and set ok (math $ok + 1)
    end

    if command -q codex
        set checked (math $checked + 1)
        if _cpen_hooks_edit $mode $HOME/.codex/hooks.json $matcher $cmd codex
            # codex 는 훅을 신뢰(trust)하기 전까지 조용히 건너뛴다. 설정 파일에
            # 적혀 있다는 것만으로 "동작한다" 고 말하면 안 되는 이유다.
            # 그래서 status 는 승인까지 확인하고, install 은 파일을 쓴 것으로 성공이되
            # 승인이 남았다는 사실을 반드시 알린다.
            if test "$mode" = status
                _cpen_hooks_codex_trusted; and set ok (math $ok + 1)
            else
                set ok (math $ok + 1)
                if test "$mode" = install; and not _cpen_hooks_codex_trusted
                    echo "cpen: codex 는 훅을 한 번 승인해야 동작합니다." >&2
                    echo "      codex 를 대화형으로 띄우면 'Hooks need review' 가 뜹니다 -" >&2
                    echo "      'Trust all and continue' 를 고르세요. 승인 전까지는 차단되지 않습니다." >&2
                end
            end
        end
    end

    if test $checked -eq 0
        test "$mode" = status; and return 1
        echo "cpen: codex 도 claude 도 찾을 수 없어 설치할 곳이 없습니다" >&2
        return 1
    end

    if test "$mode" = status
        # 하나라도 빠졌으면 "설치됨" 이라고 말하지 않는다.
        test $ok -eq $checked; and return 0
        return 1
    end

    test $ok -eq $checked
end

function _cpen_hooks_codex_trusted --description "codex 가 전역 PreToolUse 훅을 승인했는지"
    # 승인하면 config.toml 에 hooks.state."<경로>:pre_tool_use:..." 항목이 생긴다.
    # 훅 내용이 바뀌면 해시가 달라져 다시 승인을 받아야 하므로, 항목 유무만 본다.
    test -r $HOME/.codex/config.toml; or return 1
    string match -q '*hooks.json:pre_tool_use*' -- (cat $HOME/.codex/config.toml | string collect)
end

function _cpen_hooks_edit --description "설정 파일 하나에 대해 install/uninstall/status 수행"
    set -l mode $argv[1]
    set -l file $argv[2]
    set -l matcher $argv[3]
    set -l cmd $argv[4]
    set -l label $argv[5]

    set -l out (python3 -c '
import json, os, sys, tempfile

mode, path, matcher, cmd = sys.argv[1:5]
MARK = "cpen-guard"

if os.path.exists(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except Exception as e:
        print("설정을 읽지 못했습니다: %s" % e)
        sys.exit(2)
else:
    doc = {}

groups = (doc.get("hooks") or {}).get("PreToolUse") or []
def is_ours(g):
    return any(MARK in (h.get("command") or "") for h in (g.get("hooks") or []))
installed = any(is_ours(g) for g in groups)

if mode == "status":
    sys.exit(0 if installed else 1)

if mode == "install":
    if installed:
        print("이미 설치됨")
        sys.exit(0)
    # 차단 판단이 다른 훅보다 먼저 서도록 앞에 붙인다.
    groups.insert(0, {"matcher": matcher,
                      "hooks": [{"type": "command", "command": cmd}]})
    doc.setdefault("hooks", {})["PreToolUse"] = groups
elif mode == "uninstall":
    if not installed:
        print("설치되어 있지 않음")
        sys.exit(0)
    kept = []
    for g in groups:
        g["hooks"] = [h for h in (g.get("hooks") or []) if MARK not in (h.get("command") or "")]
        if g["hooks"]:
            kept.append(g)
    hooks = doc.get("hooks") or {}
    if kept:
        hooks["PreToolUse"] = kept
    else:
        hooks.pop("PreToolUse", None)
    if hooks:
        doc["hooks"] = hooks
    else:
        doc.pop("hooks", None)
else:
    print("알 수 없는 모드: %s" % mode)
    sys.exit(2)

# 남의 설정 파일이다. 처음 손대기 전에 원본을 남긴다.
backup = path + ".cpen-backup"
if os.path.exists(path) and not os.path.exists(backup):
    with open(path) as src, open(backup, "w") as dst:
        dst.write(src.read())

os.makedirs(os.path.dirname(path), exist_ok=True)
# 같은 디렉토리에 쓰고 rename 해야 원자적이다. 중간에 죽어도 반쪽 설정이 남지 않는다.
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    json.dump(doc, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("완료")
' $mode $file $matcher $cmd)
    set -l rc $status

    if test "$mode" = status
        return $rc
    end

    if test $rc -ne 0
        echo "cpen: $label 훅 설정 실패 ($file): $out" >&2
        return 1
    end

    echo "cpen: $label $out ($file)"
    return 0
end
