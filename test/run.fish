#!/usr/bin/env fish
# 전체 테스트 실행.
#   fish test/run.fish

set -l here (path dirname (status filename))
set -l failed 0

for suite in $here/test_*.fish
    echo "### "(path basename $suite)
    fish $suite; or set failed (math $failed + 1)
    echo
end

if test $failed -gt 0
    echo "실패한 스위트 $failed 개"
    exit 1
end
echo "모든 스위트 통과"
