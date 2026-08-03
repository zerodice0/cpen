function _cpen_hooks --description "가드/포커스 훅 설치/제거/조회"
    # 훅을 *전역* 설정에만 넣는다. 프로젝트 훅(.codex/hooks.json 등)은 신뢰 승인을
    # 받아야 하고 headless 실행에서는 조용히 무시되어, 있는 줄 알았는데 안 걸리는
    # 최악의 실패로 이어진다.
    set -l mode $argv[1]

    set -l ok 0
    set -l checked 0

    if command -q claude
        set checked (math $checked + 1)
        _cpen_hooks_edit $mode $HOME/.claude/settings.json claude
        and set ok (math $ok + 1)
    end

    if command -q codex
        set checked (math $checked + 1)
        if _cpen_hooks_edit $mode $HOME/.codex/hooks.json codex
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
    # 차단이 걸렸는지가 곧 가드(PreToolUse) 승인 여부다. 포커스(Stop) 훅은 승인
    # 전이어도 못 뜨는 게 전부라 여기서 따지지 않는다.
    # 승인하면 config.toml 에 hooks.state."<경로>:pre_tool_use:..." 항목이 생긴다.
    # 훅 내용이 바뀌면 해시가 달라져 다시 승인을 받아야 하므로, 항목 유무만 본다.
    test -r $HOME/.codex/config.toml; or return 1
    string match -q '*hooks.json:pre_tool_use*' -- (cat $HOME/.codex/config.toml | string collect)
end

function _cpen_hooks_edit --description "설정 파일 하나에 대해 install/uninstall/status 수행"
    set -l mode $argv[1]
    set -l file $argv[2]
    set -l label $argv[3]

    # 훅 명령을 fish 쪽에서 만드는 이유는 인용이다. 아래 파이썬 소스는 fish 의
    # 작은따옴표 안에 통째로 들어가 있어 그 안에서 작은따옴표를 쓸 수 없다.
    #
    # 훅은 stdin 으로 payload 를 받는 명령이고, 우리 함수는 autoload 되므로
    # 파일 경로 대신 fish -c 로 부른다 - fisher 로 설치해도 경로가 안 깨진다.
    set -l guard_cmd "fish -c cpen-guard"

    # 포커스 훅은 Stop 이라 *모든* 세션에서 매 턴 불린다. 그래서 판정을 fish 까지
    # 끌고 가지 않고 sh 단계에서 끝낸다 - cpen 밖 세션에서는 fish 조차 뜨지 않는다
    # (실측 턴당 35ms -> 5ms). $CPEN_PEN_FILE 은 cpen 이 에이전트 프로세스에만
    # env 로 넣어주는 변수라, 그 유무가 곧 "cpen 이 띄운 세션인가" 다.
    #
    # sh -c 로 감싸는 형태는 에이전트가 명령을 sh 에 통째로 넘기든 인자로 쪼개
    # 실행하든 똑같이 동작한다. exec 는 판정이 끝난 sh 를 남겨두지 않으려는 것이고,
    # || true 는 변수가 없을 때 [ 가 내는 1 을 삼킨다 - Stop 훅의 non-zero 는
    # claude 가 "종료를 막았다" 로 읽어 턴을 이어간다.
    set -l focus_cmd "sh -c '[ -n \"\$CPEN_PEN_FILE\" ] && exec fish -c \"cpen-focus --if-touched\" || true'"

    set -l out (python3 -c '
import json, os, sys, tempfile

mode, path, guard_cmd, focus_cmd = sys.argv[1:5]

# 설치할 훅 명세. mark 는 command 안에 반드시 들어 있는 문자열이라,
# 나중에 우리 항목만 골라내고 명령이 바뀌었는지도 알아볼 수 있다.
HOOKS = [
    # 차단 판단이 남의 훅보다 먼저 서도록 맨 앞에 넣는다.
    # claude 는 mcp__pencil__execute, codex 는 pencil/execute 로 도구를 부른다.
    # 둘 다 걸리도록 매처는 느슨하게 잡고, 진짜 판정은 훅 안에서 filePath 로 한다.
    {"event": "PreToolUse", "matcher": ".*[Pp]encil.*", "mark": "cpen-guard",
     "command": guard_cmd, "first": True},
    # 턴이 끝나면 작업 대상 .pen 을 Pen.app 최전면으로 올린다.
    # 알림 같은 남의 Stop 훅을 앞지를 이유가 없어 뒤에 붙인다.
    {"event": "Stop", "matcher": None, "mark": "cpen-focus",
     "command": focus_cmd, "first": False},
]
GUARD = HOOKS[0]

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

def groups_of(spec):
    return hooks.get(spec["event"]) or []

def installed(spec):
    return any(any(spec["mark"] in (h.get("command") or "")
                   for h in (g.get("hooks") or []))
               for g in groups_of(spec))

if mode == "status":
    # 동시 편집 차단이 곧 가드다. 포커스 훅 유무로 이 판정을 뒤집지 않는다.
    sys.exit(0 if installed(GUARD) else 1)

changed = []
if mode == "install":
    for spec in HOOKS:
        # 우리 항목인데 명령만 낡은 경우 - 판정 방식이 바뀌면 여기로 온다.
        # 그대로 두면 사용자가 uninstall/install 을 돌아야 갱신되므로 자리에서 고친다.
        stale = [h for g in groups_of(spec) for h in (g.get("hooks") or [])
                 if spec["mark"] in (h.get("command") or "")
                 and h.get("command") != spec["command"]]
        if stale:
            for h in stale:
                h["command"] = spec["command"]
            changed.append(spec["mark"] + "(갱신)")
            continue
        if installed(spec):
            continue
        entry = {}
        if spec["matcher"]:
            entry["matcher"] = spec["matcher"]
        entry["hooks"] = [{"type": "command", "command": spec["command"]}]
        groups = groups_of(spec)
        groups.insert(0, entry) if spec["first"] else groups.append(entry)
        hooks[spec["event"]] = groups
        changed.append(spec["mark"])
elif mode == "uninstall":
    for spec in HOOKS:
        if not installed(spec):
            continue
        kept = []
        for g in groups_of(spec):
            g["hooks"] = [h for h in (g.get("hooks") or [])
                          if spec["mark"] not in (h.get("command") or "")]
            if g["hooks"]:
                kept.append(g)
        if kept:
            hooks[spec["event"]] = kept
        else:
            hooks.pop(spec["event"], None)
        changed.append(spec["mark"])
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
print("완료: %s" % ", ".join(changed))
' $mode $file $guard_cmd $focus_cmd)
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
