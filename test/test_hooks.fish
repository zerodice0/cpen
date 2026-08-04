#!/usr/bin/env fish
# 자동 저장 훅 설치/제거가 멱등이고 다른 설정을 보존하는지 검증한다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/_cpen_hooks.fish
source $root/functions/cpen-save.fish

set -g TMP (mktemp -d)
set -g FAILED 0

function expect_status -a label expected actual
    if test "$actual" = "$expected"
        echo "  ok   $label"
    else
        echo "  FAIL $label (exit=$actual, 기대=$expected)"
        set -g FAILED (math $FAILED + 1)
    end
end

mkdir -p $TMP/.claude $TMP/.codex
printf '%s' '{"model":"opus","hooks":{"PreToolUse":[{"matcher":".*Pencil.*","hooks":[{"type":"command","command":"fish -c cpen-guard"}]}],"PostToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"echo hi"}]}]}}' >$TMP/.claude/settings.json
printf '%s' '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"fish -c cpen-focus --if-touched"}]}],"SessionStart":[{"hooks":[{"type":"command","command":"notify.sh"}]}]}}' >$TMP/.codex/hooks.json
set -gx HOME $TMP

echo "== 자동 저장 훅 설치 =="

_cpen_hooks status
expect_status "설치 전 status 는 실패" 1 $status

_cpen_hooks install >/dev/null 2>&1
expect_status "install" 0 $status

_cpen_hooks status
expect_status "설치 후 status 는 성공" 0 $status

_cpen_hooks install >/dev/null 2>&1
expect_status "install 재실행도 성공" 0 $status

python3 -c '
import json, sys
for path in sys.argv[1:]:
    doc = json.load(open(path))
    commands = [h.get("command", "")
                for groups in (doc.get("hooks") or {}).values()
                for group in (groups or [])
                for h in (group.get("hooks") or [])]
    saves = [c for c in commands if "cpen-save" in c]
    assert saves == ["fish -c \"cpen-save --hook\""], saves
    assert not any("cpen-guard" in c or "cpen-focus" in c for c in commands), commands
claude = json.load(open(sys.argv[1]))
codex = json.load(open(sys.argv[2]))
assert claude["model"] == "opus"
assert claude["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "echo hi"
assert codex["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "notify.sh"
' $TMP/.claude/settings.json $TMP/.codex/hooks.json
expect_status "자동 저장은 하나만 설치하고 다른 설정은 보존한다" 0 $status

test -f $TMP/.claude/settings.json.cpen-backup
expect_status "원본 백업이 남는다" 0 $status

set -l hook_output (cpen-save --hook)
test "$hook_output" = '{}'
expect_status "cpen 밖에서는 Stop 훅이 빈 JSON으로 끝난다" 0 $status

echo "== 훅 제거 =="

_cpen_hooks uninstall >/dev/null
expect_status "uninstall" 0 $status

_cpen_hooks status
expect_status "제거 후 status 는 실패" 1 $status

_cpen_hooks uninstall >/dev/null
expect_status "uninstall 재실행도 성공" 0 $status

python3 -c '
import json, sys
for path in sys.argv[1:]:
    doc = json.load(open(path))
    commands = [h.get("command", "")
                for groups in (doc.get("hooks") or {}).values()
                for group in (groups or [])
                for h in (group.get("hooks") or [])]
    assert not any("cpen-save" in c or "cpen-guard" in c or "cpen-focus" in c
                   for c in commands), commands
assert json.load(open(sys.argv[1]))["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "echo hi"
assert json.load(open(sys.argv[2]))["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "notify.sh"
' $TMP/.claude/settings.json $TMP/.codex/hooks.json
expect_status "cpen 훅만 제거한다" 0 $status

rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
