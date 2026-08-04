#!/usr/bin/env fish
# --file 이 선택 UI 없이 정확한 파일을 에이전트에 전달하는지 검증한다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/cpen.fish

set -g TMP (mktemp -d)
set -g FAILED 0
mkdir -p $TMP/repo/design
touch $TMP/repo/.git $TMP/repo/design/a.pen

function git
    echo $TMP/repo
end

function fzf
    echo called >$TMP/fzf.log
    return 1
end

function uuidgen
    echo token
end

function _cpen_lease
    return 0
end

function open
    printf '%s\n' $argv >$TMP/open.log
    return 0
end

function env
    printf '%s\n' $argv >$TMP/env.log
    return 0
end

cpen -a codex --file $TMP/repo/design/a.pen 직접지정 >/dev/null

if not test -e $TMP/fzf.log
    echo "  ok   --file 은 fzf를 건너뛴다"
else
    echo "  FAIL --file 에서 fzf를 호출했다"
    set -g FAILED (math $FAILED + 1)
end

if grep -q (path resolve $TMP/repo/design/a.pen) $TMP/env.log; and grep -q -- '-C' $TMP/env.log
    echo "  ok   절대 filePath와 git 루트를 전달한다"
else
    echo "  FAIL filePath 또는 작업 경로 전달"
    set -g FAILED (math $FAILED + 1)
end

rm -f $TMP/open.log
set -lx CPEN_SKIP_OPEN 1
cpen -a codex --file $TMP/repo/design/a.pen Herdr >/dev/null
if not test -e $TMP/open.log
    echo "  ok   Herdr 실행은 Pen을 두 번 열지 않는다"
else
    echo "  FAIL Herdr 실행에서 Pen을 다시 열었다"
    set -g FAILED (math $FAILED + 1)
end
set -e CPEN_SKIP_OPEN

cpen -a codex --file $TMP/repo/design/missing.pen >/dev/null 2>$TMP/error.log
if test $status -ne 0; and grep -q '찾을 수 없습니다' $TMP/error.log
    echo "  ok   없는 파일은 명확히 거부한다"
else
    echo "  FAIL 없는 파일 검증"
    set -g FAILED (math $FAILED + 1)
end

rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
