#!/usr/bin/env fish
# 리스 획득/해제/stale 처리 단위 테스트.
#   fish test/test_lease.fish

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/_cpen_lease.fish

set -g TMP (mktemp -d)
set -gx CPEN_LEASE_DIR $TMP/leases
set -g FAILED 0

function ok -a label
    echo "  ok   $label"
end

function fail -a label detail
    echo "  FAIL $label"
    test -n "$detail"; and echo "       $detail"
    set -g FAILED (math $FAILED + 1)
end

function expect_status -a label expected actual
    if test "$actual" = "$expected"
        ok "$label"
    else
        fail "$label" "exit=$actual, 기대=$expected"
    end
end

# 리스는 owner pid 가 codex/claude 일 때만 살아있다고 본다.
# 그래서 그 이름으로 실행되는 가짜 프로세스를 만들어 쓴다.
mkdir -p $TMP/bin
ln -sf /bin/sleep $TMP/bin/claude
ln -sf /bin/sleep $TMP/bin/codex

function spawn_fake -a name
    $TMP/bin/$name 120 >/dev/null 2>&1 &
    echo $last_pid
end

echo "== 리스 =="

set -l pid_a (spawn_fake claude)
set -l pid_b (spawn_fake codex)

_cpen_lease acquire /tmp/a.pen claude "pen:a" tok-A $pid_a
expect_status "빈 상태에서 획득" 0 $status

_cpen_lease acquire /tmp/a.pen codex "pen:a2" tok-B $pid_b
expect_status "점유 중이면 재획득 실패" 1 $status

_cpen_lease acquire "/tmp/공 백/이름 있는.pen" codex "pen:b" tok-C $pid_b
expect_status "공백 있는 경로도 획득" 0 $status

set -l info (_cpen_lease owner /tmp/a.pen)
set -l f (string split \t -- $info)
# 경로는 정규화되어 저장된다(/tmp -> /private/tmp). 나머지는 준 값 그대로.
if test "$f[1]" = (path resolve /tmp/a.pen) -a "$f[2]" = "$pid_a" -a "$f[5]" = tok-A
    ok "owner 가 기록한 값을 돌려준다"
else
    fail "owner 필드" "$info"
end

# 같은 파일을 가리키는 다른 표기로 들어와도 같은 리스여야 한다.
# 이게 깨지면 심볼릭 링크나 상대 경로로 들어온 두 세션이 서로를 못 본다.
_cpen_lease acquire (path resolve /tmp/a.pen) codex "pen:dup" tok-DUP $pid_b
expect_status "정규화 다른 표기도 같은 리스로 본다" 1 $status

test (_cpen_lease list | count) -eq 2
expect_status "list 는 살아있는 리스만" 0 $status

_cpen_lease release /tmp/a.pen tok-WRONG
expect_status "남의 토큰으로는 해제 못 함" 1 $status

_cpen_lease owner /tmp/a.pen >/dev/null
expect_status "거부된 해제 뒤에도 리스는 남아있다" 0 $status

_cpen_lease release /tmp/a.pen tok-A
expect_status "자기 토큰으로 해제" 0 $status

_cpen_lease owner /tmp/a.pen >/dev/null
expect_status "해제된 리스는 조회되지 않는다" 1 $status

# owner 프로세스를 죽이면 리스는 근거를 잃는다.
kill $pid_b 2>/dev/null
sleep 0.4

_cpen_lease owner "/tmp/공 백/이름 있는.pen" >/dev/null
expect_status "죽은 프로세스의 리스는 만료" 1 $status

set -l pid_c (spawn_fake claude)
_cpen_lease acquire "/tmp/공 백/이름 있는.pen" claude "pen:b2" tok-D $pid_c
expect_status "만료된 리스는 다시 잡을 수 있다" 0 $status

_cpen_lease reap
test (_cpen_lease list | count) -eq 1
expect_status "reap 후에도 살아있는 리스는 유지" 0 $status

kill $pid_a $pid_c 2>/dev/null
rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
