#!/usr/bin/env fish
# 구버전 설정에 cpen-guard 훅이 남아 있어도 모든 호출을 통과시키는지 검증한다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/cpen-guard.fish

set -g FAILED 0

function check_pass -a label payload
    printf '%s' $payload | cpen-guard >/dev/null 2>/dev/null
    if test $status -eq 0
        echo "  ok   $label"
    else
        echo "  FAIL $label"
        set -g FAILED (math $FAILED + 1)
    end
end

echo "== 구버전 가드 호환 =="
check_pass "Pencil execute 도 차단하지 않는다" '{"tool_name":"mcp__pencil__execute","tool_input":{"filePath":"/tmp/a.pen","input":"x"}}'
check_pass "알 수 없는 도구도 차단하지 않는다" '{"tool_name":"mcp__pencil__future_tool","tool_input":{"filePath":"/tmp/a.pen"}}'
check_pass "깨진 payload 도 차단하지 않는다" 'not json'

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
