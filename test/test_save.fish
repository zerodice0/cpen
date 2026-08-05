#!/usr/bin/env fish
# cpen-save가 정확한 파일과 포커스 복원 AppleScript를 사용하는지 검증한다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/cpen-save.fish

set -g TMP (mktemp -d)
set -g FAILED 0
set -l pen_file "$TMP/design space.pen"
touch "$pen_file"

function osascript
    printf '%s\n' $argv >$TMP/osascript.args
    echo (set -q MOCK_OSASCRIPT_RESULT; and echo $MOCK_OSASCRIPT_RESULT; or echo saved)
    return (set -q MOCK_OSASCRIPT_STATUS; and echo $MOCK_OSASCRIPT_STATUS; or echo 0)
end

set -gx CPEN_PEN_FILE "$pen_file"
set -l output (cpen-save)
if test $status -eq 0; and string match -q 'cpen: 저장 완료*' -- $output
    echo "  ok   CPEN_PEN_FILE을 저장한다"
else
    echo "  FAIL CPEN_PEN_FILE 저장"
    set -g FAILED (math $FAILED + 1)
end

if grep -q 'AXRaise' $TMP/osascript.args; and \
        grep -q 'previousProcess' $TMP/osascript.args; and \
        grep -q 'windowNames does not contain targetURL' $TMP/osascript.args; and \
        grep -q 'return "window-unavailable"' $TMP/osascript.args; and \
        grep -q 'menu item "Save"' $TMP/osascript.args; and \
        grep -qF "$pen_file" $TMP/osascript.args
    echo "  ok   대상 창이 있을 때만 저장 후 이전 앱 포커스를 복원한다"
else
    echo "  FAIL 저장 AppleScript"
    set -g FAILED (math $FAILED + 1)
end

set -g MOCK_OSASCRIPT_RESULT window-unavailable
set -l output (cpen-save)
if test $status -eq 0; and string match -q 'cpen: 저장 건너뜀*' -- $output
    echo "  ok   잠금 또는 대상 창 부재는 오류 없이 건너뛴다"
else
    echo "  FAIL 대상 창 부재 처리"
    set -g FAILED (math $FAILED + 1)
end

set -l output (cpen-save --hook)
if test $status -eq 0; and test "$output" = '{}'
    echo "  ok   Stop 훅에 유효한 JSON을 반환한다"
else
    echo "  FAIL Stop 훅 JSON"
    set -g FAILED (math $FAILED + 1)
end

set -g MOCK_OSASCRIPT_STATUS 1
cpen-save --hook >/dev/null 2>&1
if test $status -eq 1
    echo "  ok   실제 AppleScript 오류는 실패로 전달한다"
else
    echo "  FAIL AppleScript 오류 전달"
    set -g FAILED (math $FAILED + 1)
end
set -e MOCK_OSASCRIPT_STATUS

function uname
    echo Linux
end

function pen
    printf '%s\n' $argv >$TMP/pen.args
    while read -l line
        echo $line >>$TMP/pen.stdin
    end
    return (set -q MOCK_PEN_STATUS; and echo $MOCK_PEN_STATUS; or echo 0)
end

set -l output (cpen-save)
if test $status -eq 0; and string match -q 'cpen: 저장 완료*' -- $output; and \
        grep -Fxq interactive $TMP/pen.args; and \
        grep -Fxq desktop $TMP/pen.args; and \
        grep -Fxq (path resolve "$pen_file") $TMP/pen.args; and \
        grep -Fxq 'save()' $TMP/pen.stdin; and \
        grep -Fxq 'exit()' $TMP/pen.stdin
    echo "  ok   Linux에서는 Pencil CLI로 정확한 파일을 저장한다"
else
    echo "  FAIL Linux Pencil CLI 저장"
    set -g FAILED (math $FAILED + 1)
end

set -l output (cpen-save --hook)
if test $status -eq 0; and test "$output" = '{}'
    echo "  ok   Linux Stop 훅도 유효한 JSON을 반환한다"
else
    echo "  FAIL Linux Stop 훅 JSON"
    set -g FAILED (math $FAILED + 1)
end

set -g MOCK_PEN_STATUS 1
cpen-save --hook >/dev/null 2>&1
if test $status -eq 1
    echo "  ok   Linux Pencil CLI 오류를 훅 실패로 전달한다"
else
    echo "  FAIL Linux Pencil CLI 오류 전달"
    set -g FAILED (math $FAILED + 1)
end
set -e MOCK_PEN_STATUS
functions -e uname pen

set -e CPEN_PEN_FILE
cpen-save >/dev/null 2>&1
if test $status -eq 1
    echo "  ok   대상 파일이 없으면 실패한다"
else
    echo "  FAIL 대상 없는 호출"
    set -g FAILED (math $FAILED + 1)
end

rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
