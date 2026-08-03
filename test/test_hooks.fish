#!/usr/bin/env fish
# 훅 설치/제거가 멱등이고 남의 설정을 보존하는지 검증한다.
#   fish test/test_hooks.fish
#
# $HOME 을 임시 디렉토리로 바꿔 실행하므로 실제 설정은 건드리지 않는다.

set -l here (path dirname (status filename))
set -l root (path resolve $here/..)
source $root/functions/_cpen_hooks.fish

set -g TMP (mktemp -d)
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

mkdir -p $TMP/.claude $TMP/.codex
# 이미 다른 훅과 설정이 들어 있는 상태에서 시작한다.
set -l claude_before '{"model":"opus","hooks":{"PostToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"echo hi"}]}]}}'
set -l codex_before '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"notify.sh"}]}]}}'
printf '%s' $claude_before >$TMP/.claude/settings.json
printf '%s' $codex_before >$TMP/.codex/hooks.json

set -gx HOME $TMP

echo "== 훅 설치 =="

_cpen_hooks status
expect_status "설치 전 status 는 실패" 1 $status

_cpen_hooks install >/dev/null 2>&1
expect_status "install" 0 $status

# codex 는 승인 전까지 훅을 건너뛴다. 파일에 적혀 있다고 성공이라 말하면 안 된다.
_cpen_hooks status
expect_status "설치했지만 codex 미승인이면 status 실패" 1 $status

# 승인하면 config.toml 에 이 항목이 생긴다.
printf '%s\n' '[hooks.state."'$TMP'/.codex/hooks.json:pre_tool_use:0:0"]' 'trusted_hash = "sha256:x"' >$TMP/.codex/config.toml

_cpen_hooks status
expect_status "승인 후 status 는 성공" 0 $status

_cpen_hooks install >/dev/null 2>&1
expect_status "install 재실행도 성공(멱등)" 0 $status

# 설치본이 PreToolUse 에 정확히 하나만 있어야 한다.
set -l n (python3 -c '
import json, sys
doc = json.load(open(sys.argv[1]))
groups = doc["hooks"]["PreToolUse"]
print(sum(1 for g in groups for h in g["hooks"] if "cpen-guard" in h["command"]))
' $TMP/.claude/settings.json)
if test "$n" = 1
    ok "재설치해도 항목이 늘지 않는다"
else
    fail "중복 설치" "cpen-guard 항목 $n 개"
end

# 자동 포커스 훅은 더 이상 설치하지 않는다.
python3 -c '
import json, sys
doc = json.load(open(sys.argv[1]))
cmds = [h["command"] for g in doc["hooks"].get("Stop", [])
        for h in g.get("hooks", [])]
assert not any("cpen-focus" in c for c in cmds), cmds
' $TMP/.codex/hooks.json
expect_status "자동 포커스 훅이 설치되지 않는다" 0 $status

# 구버전 설치본에 남은 자동 포커스 훅도 재설치 시 제거한다.
python3 -c '
import json, sys
path = sys.argv[1]
doc = json.load(open(path))
doc.setdefault("hooks", {})["Stop"] = [{"hooks": [
    {"type": "command", "command": "fish -c cpen-focus-OLD"}
]}]
json.dump(doc, open(path, "w"))
' $TMP/.codex/hooks.json
_cpen_hooks install >/dev/null 2>&1
python3 -c '
import json, sys
doc = json.load(open(sys.argv[1]))
cmds = [h["command"] for g in doc["hooks"].get("Stop", [])
        for h in g.get("hooks", [])]
assert not any("cpen-focus" in c for c in cmds), cmds
' $TMP/.codex/hooks.json
expect_status "구버전 자동 포커스 훅을 재설치로 제거한다" 0 $status

# 기존 훅이 살아있어야 한다.
python3 -c '
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["model"] == "opus"
assert doc["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "echo hi"
' $TMP/.claude/settings.json
expect_status "설치가 기존 설정을 보존한다" 0 $status

test -f $TMP/.claude/settings.json.cpen-backup
expect_status "원본 백업이 남는다" 0 $status

echo "== 훅 제거 =="

_cpen_hooks uninstall >/dev/null
expect_status "uninstall" 0 $status

_cpen_hooks status
expect_status "제거 후 status 는 실패" 1 $status

_cpen_hooks uninstall >/dev/null
expect_status "uninstall 재실행도 성공(멱등)" 0 $status

# 제거 후에는 손대기 전과 의미상 같아야 한다.
set -l after (python3 -c '
import json, sys
print(json.dumps(json.load(open(sys.argv[1])), sort_keys=True))
' $TMP/.claude/settings.json)
set -l before (printf '%s' $claude_before | python3 -c '
import json, sys
print(json.dumps(json.load(sys.stdin), sort_keys=True))
')
if test "$after" = "$before"
    ok "제거하면 원래 설정으로 돌아온다 (claude)"
else
    fail "제거 후 claude 설정" "$after"
end

set -l after (python3 -c '
import json, sys
print(json.dumps(json.load(open(sys.argv[1])), sort_keys=True))
' $TMP/.codex/hooks.json)
set -l before (printf '%s' $codex_before | python3 -c '
import json, sys
print(json.dumps(json.load(sys.stdin), sort_keys=True))
')
if test "$after" = "$before"
    ok "제거하면 원래 설정으로 돌아온다 (codex)"
else
    fail "제거 후 codex 설정" "$after"
end

rm -rf $TMP

if test $FAILED -gt 0
    echo "실패 $FAILED 건"
    exit 1
end
echo "전부 통과"
