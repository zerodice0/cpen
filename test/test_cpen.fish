#!/usr/bin/env fish
# 점유 파일도 차단하지 않고 동시 작업 안내를 초기 프롬프트에 넣는지 검증한다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
set -g TMP (mktemp -d)
set -g FAILED 0
mkdir -p $TMP/design
touch $TMP/design/a.pen
mkdir -p $TMP/fish/functions
cp $root/functions/cpen.fish $TMP/fish/functions/cpen.fish
set -gx XDG_CONFIG_HOME $TMP/config
set -l binding_script \
    $XDG_CONFIG_HOME/herdr/plugins/github/zerodice0.cpen-test/herdr/cpen_session.py
mkdir -p (path dirname $binding_script)
touch $binding_script
source $TMP/fish/functions/cpen.fish

function git
    echo $TMP
end

function fd
    echo $TMP/design/a.pen
end

function fzf
    read -l picked
    echo $picked
end

function uuidgen
    echo token
end

function _cpen_occupants
    printf '%s\t%s\t%s\t%s\n' (path resolve $TMP/design/a.pen) 123 codex pen:기존세션
end

function _cpen_lease
    switch $argv[1]
        case acquire
            return 1
        case release
            return 0
    end
end

function open
    return 0
end

function env
    printf '%s\n' $argv >$TMP/env.log
    return 0
end

function python3
    printf '%s\n' $argv >$TMP/bind.log
end

set -gx CPEN_AGENT codex
set -gx CPEN_EXTERNAL_OCCUPANTS "w1:p4"
set -gx HERDR_PANE_ID "w1:p9"
cpen 동시작업 >/dev/null 2>$TMP/err.log
set -l rc $status

if test $rc -eq 0
    echo "  ok   점유 파일도 바로 시작한다"
else
    echo "  FAIL 점유 파일 시작 (exit=$rc)"
    set -g FAILED (math $FAILED + 1)
end

if grep -q '동시 작업으로 시작합니다' $TMP/err.log
    echo "  ok   셸에는 동시 작업 상태를 알린다"
else
    echo "  FAIL 동시 작업 상태 안내"
    set -g FAILED (math $FAILED + 1)
end

if grep -q '현재 같은 파일을 codex(pid 123, "pen:기존세션")' $TMP/env.log; and \
        grep -q '연결된 Herdr pane이 있습니다: w1:p4' $TMP/env.log; and \
        grep -q '수정 직전에 대상 노드를 다시 읽고' $TMP/env.log; and \
        grep -q '다른 세션의 변경을 덮어쓰거나 되돌리지 마세요' $TMP/env.log
    echo "  ok   초기 프롬프트에 간섭 방지 지침을 넣는다"
else
    echo "  FAIL 초기 프롬프트 내용"
    cat $TMP/env.log
    set -g FAILED (math $FAILED + 1)
end

if grep -q 'PreToolUse\|차단합니다' $TMP/env.log
    echo "  FAIL 초기 프롬프트에 차단 안내가 남아있다"
    set -g FAILED (math $FAILED + 1)
else
    echo "  ok   초기 프롬프트에 차단 안내가 없다"
end

if grep -Fxq "$binding_script" $TMP/bind.log; and \
        grep -q '^bind$' $TMP/bind.log; and \
        grep -q '^w1:p9$' $TMP/bind.log; and \
        grep -q (path resolve $TMP/design/a.pen) $TMP/bind.log
    echo "  ok   Herdr pane과 .pen 연결을 기록한다"
else
    echo "  FAIL Herdr pane binding 기록"
    cat $TMP/bind.log
    set -g FAILED (math $FAILED + 1)
end

rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
