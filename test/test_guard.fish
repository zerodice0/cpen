#!/usr/bin/env fish
# PreToolUse 가드 훅 판정 테스트.
#   fish test/test_guard.fish

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/_cpen_lease.fish
source $root/functions/cpen-guard.fish

set -g TMP (mktemp -d)
set -gx CPEN_LEASE_DIR $TMP/leases
set -g FAILED 0

mkdir -p $TMP/bin $TMP/design $TMP/json
ln -sf /bin/sleep $TMP/bin/claude
ln -sf /bin/sleep $TMP/bin/codex
touch $TMP/design/a.pen $TMP/design/b.pen

# 훅 payload 는 인용이 까다로워 파일로 만들어 두고 리다이렉션으로 먹인다.
function fixture -a name json
    printf '%s' $json >$TMP/json/$name.json
end

fixture bash '{"tool_name":"Bash","tool_input":{"command":"ls"}}'
fixture appstate '{"tool_name":"mcp__pencil__get_app_state","tool_input":{"include_schema":true}}'
fixture broken 'not json at all'
fixture noinput '{"tool_name":"mcp__pencil__execute"}'
# 에이전트마다 MCP 도구 이름 표기가 다르다. 어느 쪽이든 같은 판정이 나와야 한다.
fixture claude_a "{\"tool_name\":\"mcp__pencil__execute\",\"tool_input\":{\"filePath\":\"$TMP/design/a.pen\",\"input\":\"x\"}}"
fixture claude_b "{\"tool_name\":\"mcp__pencil__execute\",\"tool_input\":{\"filePath\":\"$TMP/design/b.pen\",\"input\":\"x\"}}"
fixture codex_a "{\"tool_name\":\"pencil/execute\",\"tool_input\":{\"filePath\":\"$TMP/design/a.pen\",\"input\":\"x\"}}"
fixture shot_a "{\"tool_name\":\"pencil__get_screenshot\",\"tool_input\":{\"filePath\":\"$TMP/design/a.pen\",\"nodeId\":\"document\"}}"
fixture shot_b "{\"tool_name\":\"mcp__pencil__get_screenshot\",\"tool_input\":{\"filePath\":\"$TMP/design/b.pen\",\"nodeId\":\"document\"}}"
fixture export_a "{\"tool_name\":\"pencil/export_nodes\",\"tool_input\":{\"filePath\":\"$TMP/design/a.pen\",\"nodeIds\":[\"x\"],\"outputDir\":\"/tmp\"}}"
# 모르는 도구는 읽기로 넘겨짚지 않고 쓰기로 본다(fail-safe).
fixture unknown_a "{\"tool_name\":\"mcp__pencil__import_tokens\",\"tool_input\":{\"filePath\":\"$TMP/design/a.pen\"}}"

function chk -a label expected name
    cpen-guard <$TMP/json/$name.json >/dev/null 2>$TMP/err.txt
    set -l st $status
    if test $st -eq $expected
        echo "  ok   $label"
    else
        echo "  FAIL $label (exit=$st, 기대=$expected)"
        head -3 $TMP/err.txt
        set -g FAILED (math $FAILED + 1)
    end
end

function spawn_fake -a name
    $TMP/bin/$name 120 >/dev/null 2>&1 &
    echo $last_pid
end

# 실제 claim 은 조상 체인에서 codex/claude 프로세스를 찾는데, 테스트에서는 fish 가
# 직접 부르므로 언제나 no-op 이 된다. 호출 자체를 기록해 "어느 파일을 잡으려 했는가" 를 본다.
function _cpen_guard_claim -a abs
    printf '%s\n' $abs >>$TMP/claim.log
end

function claimed -a path
    # 가드는 정규화한 경로로 claim 한다. macOS 는 /var 가 /private/var 심볼릭 링크라
    # 정규화하지 않고 비교하면 항상 어긋난다.
    set -l lines (cat $TMP/claim.log 2>/dev/null)
    contains -- (path resolve $path) $lines
end

echo "== 가드 훅 =="

set -l other (spawn_fake codex)

echo "-- .pen 을 건드리지 않는 호출 --"
chk "Bash 는 통과" 0 bash
chk "filePath 없는 get_app_state 는 통과" 0 appstate
chk "깨진 payload 는 통과" 0 broken
chk "tool_input 없는 호출은 통과" 0 noinput

echo "-- 배정 밖 파일 --"
# .pen 은 imports 로 다른 파일의 토큰을 끌어다 쓴다. 참조 대상을 읽지 못하면
# 토큰 일원화가 불가능하므로, 배정 밖이라는 이유만으로 막지 않는다.
set -gx CPEN_PEN_FILE $TMP/design/a.pen
chk "배정된 파일은 통과" 0 claude_a
chk "배정 밖 파일도 읽기는 통과" 0 shot_b
chk "배정 밖 파일 수정은 아무도 안 잡고 있으면 통과" 0 claude_b

# 배정 밖 파일은 리스를 잡지 않는다 - 스쳐 간 세션이 공용 토큰 파일을 점유하면 안 된다.
if claimed $TMP/design/a.pen
    echo "  ok   배정 파일은 리스를 잡는다"
else
    echo "  FAIL 배정 파일을 잡지 않았다"
    set -g FAILED (math $FAILED + 1)
end
if claimed $TMP/design/b.pen
    echo "  FAIL 배정 밖 파일을 점유했다"
    set -g FAILED (math $FAILED + 1)
else
    echo "  ok   배정 밖 파일은 리스를 잡지 않는다"
end
rm -f $TMP/claim.log
set -e CPEN_PEN_FILE
rm -rf $CPEN_LEASE_DIR

echo "-- 다른 세션이 점유 중 --"
_cpen_lease acquire $TMP/design/a.pen codex "pen:a" tok-OTHER $other
chk "claude 표기로도 수정은 차단" 2 claude_a
chk "codex 표기로도 수정은 차단" 2 codex_a
chk "읽기 도구(get_screenshot)는 통과" 0 shot_a
chk "읽기 도구(export_nodes)도 통과" 0 export_a
chk "모르는 도구는 쓰기로 보고 차단" 2 unknown_a
chk "점유되지 않은 파일은 통과" 0 claude_b

echo "-- 내 리스 --"
set -gx CPEN_LEASE_TOKEN tok-OTHER
chk "토큰이 같으면 통과" 0 claude_a
set -e CPEN_LEASE_TOKEN

echo "-- 점유자 사망 --"
kill $other 2>/dev/null
sleep 0.4
chk "만료된 리스는 통과" 0 claude_a

echo "-- 차단 메시지 --"
set -l live (spawn_fake claude)
rm -rf $CPEN_LEASE_DIR
_cpen_lease acquire $TMP/design/a.pen claude "pen:작업중" tok-Z $live
cpen-guard <$TMP/json/claude_a.json >/dev/null 2>$TMP/err.txt
if grep -q "pen:작업중" $TMP/err.txt; and grep -q "다시 시도하지" $TMP/err.txt
    echo "  ok   점유 세션과 행동 지침이 메시지에 담긴다"
else
    echo "  FAIL 차단 메시지 내용"
    cat $TMP/err.txt
    set -g FAILED (math $FAILED + 1)
end

kill $live 2>/dev/null
rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
