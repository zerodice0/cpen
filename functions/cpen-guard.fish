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

    set -l tool $parsed[1]
    set -l file $parsed[2]

    string match -q '*.pen' -- "$file"; or return 0

    # 훅은 에이전트의 cwd 에서 실행되지만 filePath 는 절대 경로 계약이다.
    # 그래도 상대 경로가 올 수 있으니 정규화해서 리스 키를 맞춘다.
    set -l abs (path resolve "$file")

    # 1) 읽기는 어떤 .pen 이든 통과시킨다.
    #    .pen 은 다른 파일을 imports 로 끌어다 variables(디자인 토큰)를 공유한다.
    #    참조 대상을 읽지 못하면 토큰 일원화 자체가 불가능하므로, 읽기를 막는 것은
    #    동시 편집 방지와 무관한 순수한 손해다. 읽기는 무엇도 덮어쓰지 않는다.
    if _cpen_guard_readonly "$tool"
        return 0
    end

    # 2) 여기부터는 파일을 바꿀 수 있는 호출이다. 리스 주인이 따로 있으면 막는다 -
    #    훅이 책임지는 규칙은 이 하나, 동시 편집 방지다.
    set -l info (_cpen_lease owner "$abs")
    if test $status -eq 0
        set -l fields (string split \t -- $info)
        set -l owner_pid $fields[2]
        set -l owner_agent $fields[3]
        set -l owner_session $fields[4]
        set -l owner_token $fields[5]

        if _cpen_guard_mine $owner_pid $owner_token
            # Stop 훅(cpen-focus)이 이 표시를 보고 Pen.app 을 앞으로 올릴지 정한다.
            _cpen_lease mark "$abs"
            return 0
        end

        _cpen_guard_deny "이 .pen 파일은 다른 에이전트 세션이 작업 중입니다.

  파일: $abs
  점유: $owner_agent (pid $owner_pid) / 세션 \"$owner_session\"

Pencil 은 같은 파일에 대한 동시 편집을 직렬화하지 않습니다. 그대로 진행하면
서로의 변경이 덮이거나 undo 스택이 뒤섞입니다.

읽기는 막히지 않으므로 참고가 목적이라면 조회 도구를 쓰세요. 수정이 목적이라면
같은 요청을 다시 시도하지 말고, 우회 경로($file 를 다른 도구로 열기 등)도 찾지 마세요.
사용자에게 위 세션이 점유 중이라고 보고하고, 끝날 때까지 기다릴지 물어보세요."
        return 2
    end

    # 3) 리스가 없는 파일이다.
    #    배정 파일이거나 cpen 밖 세션이면 여기서 잡아 이후 다른 세션을 막는다.
    #    배정 밖 파일은 잡지 않는다 - 토큰 파일처럼 여러 세션이 함께 참조하는 파일을
    #    스쳐 지나간 세션이 통째로 점유해 버리면 안 된다. 그 대신 "수정은 배정 파일에만"
    #    은 프롬프트 계약이 맡는다. 훅이 강제하는 것은 동시 편집 방지뿐이다.
    if test -z "$CPEN_PEN_FILE"; or test "$abs" = (path resolve "$CPEN_PEN_FILE")
        _cpen_guard_claim "$abs"
        # Stop 훅(cpen-focus)이 이 표시를 보고 Pen.app 을 앞으로 올릴지 정한다.
        _cpen_lease mark "$abs"
    end

    # 표시나 claim 이 실패해도 호출은 통과시킨다. 여기서 non-zero 를 내면
    # 부가 기능의 실패가 도구 호출 자체를 막는다.
    return 0
end

function _cpen_guard_mine --description "이 리스의 주인이 이 세션인가" -a owner_pid owner_token
    # 토큰만 보면 cpen 밖 세션이 자기 리스에 막히고, pid 만 보면 토큰을 넘겨받은
    # 서브에이전트 호출을 놓친다. 둘 다 본다.
    if test -n "$CPEN_LEASE_TOKEN"; and test "$owner_token" = "$CPEN_LEASE_TOKEN"
        return 0
    end
    contains -- $owner_pid (_cpen_guard_ancestors)
end

function _cpen_guard_readonly --description "파일을 바꾸지 않는 Pencil 도구인가" -a tool
    # 화이트리스트로 두는 이유는 fail-safe 다. Pencil 에 새 도구가 생겼을 때
    # 모르는 이름을 읽기로 보고 통과시키는 쪽보다, 쓰기로 보고 막는 쪽이 안전하다.
    #
    # execute 는 여기 없다. 문서상 input 이 자유 형식 코드라 조회만 하는지
    # 정적으로 보장할 수 없다 - 실제로 Pencil 은 구조 조회에도 execute 의 Get 을
    # 권한다. 읽기 겸용이라는 이유로 열어 주면 쓰기가 통째로 열린다.
    #
    # 도구 이름 표기는 에이전트마다 다르다(claude 는 mcp__pencil__get_screenshot,
    # codex 는 pencil/get_screenshot). 그래서 접미사로 맞춘다.
    string match -qr '(^|[_/.])(get_screenshot|export_nodes|export_html)$' -- "$tool"
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
