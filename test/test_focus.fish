#!/usr/bin/env fish
# 턴 종료 포커스 판정 테스트.
#   fish test/test_focus.fish
#
# 실제로 Pen.app 을 띄우면 테스트가 화면을 뺏는다. open 을 함수로 가려 호출만 남긴다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/_cpen_lease.fish
source $root/functions/cpen-focus.fish

set -g TMP (mktemp -d)
set -gx CPEN_LEASE_DIR $TMP/leases
set -g FAILED 0

mkdir -p $TMP/bin $TMP/design
ln -sf /bin/sleep $TMP/bin/claude
touch $TMP/design/a.pen

# fish 는 함수를 외부 명령보다 먼저 찾으므로 cpen-focus 안의 open 이 이걸 부른다.
function open
    printf '%s\n' "$argv" >>$TMP/open.log
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

# 훅 경로는 stdin 으로 payload 를 받는다. 실제 호출과 같은 모양으로 먹인다.
function hook_run
    printf '%s' '{"session_id":"x","stop_hook_active":false}' | cpen-focus --if-touched
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

echo "== 포커스 훅 =="

echo "-- cpen 밖 세션 --"
set -e CPEN_PEN_FILE
hook_run
set -l st $status
test $st -eq 0; and ok "대상이 없으면 exit 0"; or fail "대상 없을 때 exit" "exit=$st"
expect_opens "대상이 없으면 포커스하지 않는다" 0

echo "-- 리스와 표시 --"
$TMP/bin/claude 120 >/dev/null 2>&1 &
set -l owner $last_pid
_cpen_lease acquire $TMP/design/a.pen claude "pen:a" tok-F $owner
set -gx CPEN_PEN_FILE $TMP/design/a.pen

hook_run
expect_opens "수정 표시가 없으면 포커스하지 않는다" 0

_cpen_lease mark $TMP/design/a.pen
hook_run
expect_opens "수정 표시가 있으면 포커스한다" 1

hook_run
expect_opens "표시는 한 번만 쓰인다" 0

echo "-- 정책 --"
_cpen_lease mark $TMP/design/a.pen
set -gx CPEN_FOCUS never
hook_run
expect_opens "never 는 표시가 있어도 포커스하지 않는다" 0

# never 가 표시를 소비하지 않았어야 auto 로 되돌렸을 때 그대로 살아 있다.
set -gx CPEN_FOCUS auto
hook_run
expect_opens "never 는 표시를 삼키지 않는다" 1

set -gx CPEN_FOCUS always
hook_run
expect_opens "always 는 표시가 없어도 포커스한다" 1
set -gx CPEN_FOCUS auto

echo "-- 직접 호출 --"
cpen-focus
expect_opens "인자 없는 직접 호출은 표시와 무관하게 포커스한다" 1

cpen-focus $TMP/design/a.pen
expect_opens "경로를 넘긴 직접 호출도 포커스한다" 1

set -e CPEN_PEN_FILE
cpen-focus >/dev/null 2>&1
set st $status
test $st -eq 1; and ok "대상이 없는 직접 호출은 exit 1"; or fail "직접 호출 exit" "exit=$st"
expect_opens "대상이 없으면 open 을 부르지 않는다" 0

echo "-- 리스가 없을 때 --"
set -gx CPEN_PEN_FILE $TMP/design/a.pen
kill $owner 2>/dev/null
sleep 0.4
rm -rf $CPEN_LEASE_DIR
hook_run
set st $status
test $st -eq 0; and ok "리스가 사라져도 exit 0"; or fail "리스 없을 때 exit" "exit=$st"
expect_opens "리스가 없으면 포커스하지 않는다" 0

rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
