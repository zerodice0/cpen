function cpen-guard --description "PreToolUse 훅 본체: .pen 을 건드리는 MCP 호출을 리스로 통제한다"
    # codex/claude 양쪽의 PreToolUse 훅에서 `fish -c cpen-guard` 로 불린다.
    # stdin 으로 {tool_name, tool_input:{filePath,...}, ...} 이 들어온다.
    #
    # 판정을 tool_name 이 아니라 filePath 확장자로 하는 이유: MCP 도구 이름 표기가
    # 에이전트마다 다르다(claude 는 mcp__pencil__execute, codex 는 pencil/execute).
    # 반면 Pencil MCP 의 쓰기/조회 도구는 전부 filePath 를 필수로 받으므로
    # "인자에 .pen 이 있으면 대상" 이 표기 방식과 무관하게 성립한다.
    # cat 을 명령치환 안에서 쓰면 안 된다 - fish 는 명령치환에 함수의 stdin 을
    # 물려주지 않아서, 훅으로 들어온 payload 대신 터미널을 기다리며 멈춘다.
    set -l payload
    read -z payload

    # 파서가 없으면 막지 않는다. 락이 고장났다고 작업을 세우는 것보다 통과시키고
    # 경고하는 쪽이 낫다 - 원래 상태(무보호)로 돌아갈 뿐이다.
    if not command -q jq; and not command -q python3
        echo "cpen-guard: jq 또는 python3 가 없어 검사를 건너뜁니다" >&2
        return 0
    end

    # 파싱 실패는 파서 부재와 다르다. 우리가 모르는 payload 모양이라는 뜻이므로
    # 조용히 통과시킨다 - 여기서 막으면 도구 전체가 죽는다.
    set -l parsed (_cpen_guard_parse "$payload")
    test (count $parsed) -ge 2; or return 0

    set -l file $parsed[2]

    string match -q '*.pen' -- "$file"; or return 0

    # 훅은 에이전트의 cwd 에서 실행되지만 filePath 는 절대 경로 계약이다.
    # 그래도 상대 경로가 올 수 있으니 정규화해서 리스 키를 맞춘다.
    set -l abs (path resolve "$file")

    # 1) cpen 이 이 세션에 배정한 파일인가.
    #    프롬프트로만 부탁하던 "다른 .pen 은 건드리지 마세요" 를 여기서 강제한다.
    if test -n "$CPEN_PEN_FILE"; and test "$abs" != (path resolve "$CPEN_PEN_FILE")
        _cpen_guard_deny "이 세션에 배정된 .pen 파일이 아닙니다.

  요청한 파일: $abs
  배정된 파일: $CPEN_PEN_FILE

같은 요청을 다시 시도하지 마세요. 배정된 파일로 작업하거나, 다른 파일이 필요하면
사용자에게 별도 cpen 세션을 띄워 달라고 요청하세요."
        return 2
    end

    # 2) 이 파일의 리스를 누가 들고 있는가.
    set -l info (_cpen_lease owner "$abs")
    if test $status -ne 0
        # 리스가 없다 - cpen 을 거치지 않았거나 resume 으로 되살린 세션이다.
        # 여기서 대신 잡아 준다. 이후 다른 세션은 이 파일에서 막힌다.
        _cpen_guard_claim "$abs"
        _cpen_guard_mark "$abs" $parsed[1]
        return 0
    end

    set -l fields (string split \t -- $info)
    set -l owner_pid $fields[2]
    set -l owner_agent $fields[3]
    set -l owner_session $fields[4]
    set -l owner_token $fields[5]

    # 내 리스인가: cpen 이 넘겨준 토큰이 같거나, 리스 주인이 내 조상 프로세스이거나.
    # 토큰만 보면 cpen 밖 세션이 자기 리스에 막히고, pid 만 보면 토큰을 넘겨받은
    # 서브에이전트 호출을 놓친다. 둘 다 본다.
    if test -n "$CPEN_LEASE_TOKEN"; and test "$owner_token" = "$CPEN_LEASE_TOKEN"
        _cpen_guard_mark "$abs" $parsed[1]
        return 0
    end
    if contains -- $owner_pid (_cpen_guard_ancestors)
        _cpen_guard_mark "$abs" $parsed[1]
        return 0
    end

    _cpen_guard_deny "이 .pen 파일은 다른 에이전트 세션이 작업 중입니다.

  파일: $abs
  점유: $owner_agent (pid $owner_pid) / 세션 \"$owner_session\"

Pencil 은 같은 파일에 대한 동시 편집을 직렬화하지 않습니다. 그대로 진행하면
서로의 변경이 덮이거나 undo 스택이 뒤섞입니다.

같은 요청을 다시 시도하지 말고, 우회 경로($file 를 다른 도구로 열기 등)도 찾지 마세요.
사용자에게 위 세션이 점유 중이라고 보고하고, 끝날 때까지 기다릴지 물어보세요."
    return 2
end

function _cpen_guard_parse --description "훅 payload 에서 tool_name 과 filePath 를 뽑는다"
    # 훅은 호출마다 새 프로세스라 파서 기동 비용이 그대로 지연이 된다.
    # jq 가 있으면 jq 로 끝내고, 없을 때만 python3 로 넘어간다.
    set -l line
    if command -q jq
        set line (printf '%s' $argv[1] | jq -r '[(.tool_name // ""), (.tool_input.filePath // "")] | @tsv' 2>/dev/null)
    else if command -q python3
        set line (printf '%s' $argv[1] | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
ti = d.get("tool_input") or {}
fp = ti.get("filePath") if isinstance(ti, dict) else ""
print("%s\t%s" % (d.get("tool_name") or "", fp or ""))
' 2>/dev/null)
    else
        return 1
    end

    test (count $line) -ge 1; or return 1
    # 탭이 없으면 string split 이 실패를 반환하지만 원소는 내놓는다.
    # 필드 수 판정은 호출부에서 하므로 여기서는 status 를 삼킨다.
    string split \t -- $line[1]
    return 0
end

function _cpen_guard_mark --description "수정 도구였다면 리스에 표시를 남긴다" -a abs tool
    # Stop 훅(cpen-focus)이 이 표시를 보고 Pen.app 을 앞으로 올릴지 정한다.
    # 표시가 없는 턴은 파일을 건드리지 않은 턴이므로 창을 뺏지 않는다.
    #
    # Pencil MCP 에서 문서를 바꾸는 도구는 execute 하나다. 표기는 에이전트마다
    # 다르지만(claude 는 mcp__pencil__execute, codex 는 pencil/execute) 끝은 늘
    # execute 다. 조회 도구를 잘못 집어도 손해는 포커스 한 번이라 느슨하게 본다.
    string match -qr '(^|[_/.])execute$' -- "$tool"; or return 0
    _cpen_lease mark "$abs"
end

function _cpen_guard_ancestors --description "훅 프로세스의 조상 pid 목록"
    set -l pid %self
    set -l out
    # 훅 -> sh -> 에이전트 순으로 몇 단계 안 되지만, 래퍼가 끼는 경우를 감안해 넉넉히 돈다.
    for i in (seq 12)
        test $pid -gt 1; or break
        set -a out $pid
        set pid (ps -p $pid -o ppid= 2>/dev/null | string trim)
        string match -qr '^\d+$' -- "$pid"; or break
    end
    printf '%s\n' $out
end

function _cpen_guard_claim --description "리스 없는 세션을 대신 등록한다"
    # cpen 을 거치지 않은 세션은 토큰이 없다. 주인은 조상 체인에서 찾은
    # 에이전트 프로세스로 잡는다 - 그 프로세스가 죽으면 리스도 자동으로 만료된다.
    set -l abs $argv[1]
    for pid in (_cpen_guard_ancestors)
        set -l comm (ps -p $pid -o comm= 2>/dev/null | string trim)
        if string match -qr '(^|/)(codex|claude)$' -- "$comm"
            set -l agent (path basename $comm)
            set -l label $CPEN_SESSION
            test -n "$label"; or set label "cpen 외부 세션"
            _cpen_lease acquire $abs $agent $label "$CPEN_LEASE_TOKEN" $pid
            return
        end
    end
end

function _cpen_guard_deny --description "차단 사유를 에이전트에게 전달한다"
    # exit 2 + stderr 가 codex/claude 공통의 차단 규약이다.
    # permissionDecision JSON 은 MCP 도구에서 무시된 전례가 있어 주 경로로 쓰지 않는다.
    echo "cpen: Pencil MCP 호출이 차단되었습니다." >&2
    echo "" >&2
    printf '%s\n' $argv[1] >&2
end
