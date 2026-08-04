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
    return 0
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
        grep -q 'menu item "Save"' $TMP/osascript.args; and \
        grep -qF "$pen_file" $TMP/osascript.args
    echo "  ok   대상 창 저장 후 이전 앱 포커스를 복원한다"
else
    echo "  FAIL 저장 AppleScript"
    set -g FAILED (math $FAILED + 1)
end

set -l output (cpen-save --hook)
if test $status -eq 0; and test "$output" = '{}'
    echo "  ok   Stop 훅에 유효한 JSON을 반환한다"
else
    echo "  FAIL Stop 훅 JSON"
    set -g FAILED (math $FAILED + 1)
end

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
