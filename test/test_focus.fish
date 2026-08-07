#!/usr/bin/env fish
# 수동 포커스 명령 테스트.
#
# 실제로 Pencil을 띄우면 테스트가 화면을 뺏는다. open을 함수로 가려 호출만 남긴다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/cpen-focus.fish

set -g TMP (mktemp -d)
set -g FAILED 0

mkdir -p $TMP/design
touch $TMP/design/a.pen

function open
    printf '%s\n' "$argv" >>$TMP/open.log
    return 0
end

function ok -a label
    echo "  ok   $label"
end

function fail -a label detail
    echo "  FAIL $label"
    test -n "$detail"; and echo "       $detail"
    set -g FAILED (math $FAILED + 1)
end

function opens
    set -l lines (cat $TMP/open.log 2>/dev/null)
    count $lines
end

function reset_log
    rm -f $TMP/open.log
end

function expect_opens -a label expected
    set -l n (opens)
    if test "$n" = "$expected"
        ok "$label"
    else
        fail "$label" "open 호출 $n 회, 기대 $expected 회"
    end
    reset_log
end

echo "== 수동 포커스 =="

echo "-- 구버전 Stop 훅 호환 --"
set -gx CPEN_PEN_FILE $TMP/design/a.pen
printf '%s' '{"session_id":"x","stop_hook_active":false}' | cpen-focus --if-touched
set -l st $status
test $st -eq 0; and ok "구버전 자동 훅 인자는 no-op"; or fail "구버전 훅 exit" "exit=$st"
expect_opens "구버전 훅은 창을 올리지 않는다" 0

echo "-- 직접 호출 --"
cpen-focus
expect_opens "환경변수 대상의 직접 호출은 포커스한다" 1

cpen-focus $TMP/design/a.pen
expect_opens "경로를 넘긴 직접 호출도 포커스한다" 1

set -e CPEN_PEN_FILE
cpen-focus >/dev/null 2>&1
set st $status
test $st -eq 1; and ok "대상이 없는 직접 호출은 exit 1"; or fail "직접 호출 exit" "exit=$st"
expect_opens "대상이 없으면 open 을 부르지 않는다" 0

rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
