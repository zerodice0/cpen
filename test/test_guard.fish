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

echo "== 가드 훅 =="

set -l other (spawn_fake codex)

echo "-- .pen 을 건드리지 않는 호출 --"
chk "Bash 는 통과" 0 bash
chk "filePath 없는 get_app_state 는 통과" 0 appstate
chk "깨진 payload 는 통과" 0 broken
chk "tool_input 없는 호출은 통과" 0 noinput

echo "-- 배정 파일 강제 --"
set -gx CPEN_PEN_FILE $TMP/design/a.pen
chk "배정된 파일은 통과" 0 claude_a
chk "배정 밖 파일은 차단" 2 claude_b
set -e CPEN_PEN_FILE
rm -rf $CPEN_LEASE_DIR

echo "-- 다른 세션이 점유 중 --"
_cpen_lease acquire $TMP/design/a.pen codex "pen:a" tok-OTHER $other
chk "claude 표기로도 차단" 2 claude_a
chk "codex 표기로도 차단" 2 codex_a
chk "읽기 도구(get_screenshot)도 차단" 2 shot_a
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
