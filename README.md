# cpen

Pencil `.pen` 파일을 골라 Pen.app 으로 열고, 그 파일을 작업 대상으로 하는
코딩 에이전트(codex / claude) 세션을 띄우는 fish 함수.

`.pen` 은 암호화 포맷이라 일반 파일 도구로 못 읽고 Pencil MCP 를 거쳐야 한다.
그래서 "어떤 파일을 작업 중인지" 를 셸이 확인할 수 없는데, `cpen` 은 이걸
**에이전트에게 넘기는 프롬프트 계약**으로 해결한다 — 모든 Pencil MCP 도구가
`filePath` 를 받으므로, 대상 지정을 활성 캔버스가 아니라 절대 경로로 고정한다.

## 요구사항

| | |
|---|---|
| OS | macOS (`open -a`, BSD `ps` 에 의존) |
| fish | 3.5 이상 (`path` 내장 사용) |
| 필수 명령 | [`fd`](https://github.com/sharkdp/fd), [`fzf`](https://github.com/junegunn/fzf) |
| 앱 | [Pencil](https://pencil.dev) (`Pen.app`) |
| 에이전트 | `codex` 또는 `claude` CLI 중 최소 하나 |
| 훅용 | `jq` 또는 `python3` (판정), `python3` (훅 설치) |

에이전트 쪽에 **Pencil MCP 서버가 등록되어 있어야 한다**. `cpen` 이 대신
설정해주지는 않는다.

## 설치

fisher:

```fish
fisher install zerodice0/cpen
```

수동(저장소를 직접 관리하고 싶을 때):

```fish
git clone https://github.com/zerodice0/cpen.git ~/src/cpen
ln -s ~/src/cpen/functions/*.fish   ~/.config/fish/functions/
ln -s ~/src/cpen/completions/*.fish ~/.config/fish/completions/
```

설치 후 [`cpen --install-hooks`](#동시-편집-차단) 를 한 번 실행하면
[동시 편집 차단](#동시-편집-차단)과 [턴 종료 포커스](#턴이-끝나면-pen-으로)가 켜진다.
예전 버전에서 올라왔다면 포커스 훅을 채워 넣기 위해 다시 한 번 실행한다(멱등).

## 사용법

```fish
cpen                          # 에이전트 선택 → 파일 선택
cpen -a claude                # 에이전트 선택 단계를 건너뛴다
cpen -a claude 이벤트 목록 개편  # 세션 이름까지 지정
cpen --help

cpen --install-hooks          # 동시 편집 차단 + 턴 종료 포커스를 켠다 (한 번만)
cpen --uninstall-hooks

cpen-focus                    # 대상 .pen 을 지금 앞으로 가져온다
cpen-focus design/a.pen       # 경로를 직접 지정할 수도 있다

set -Ux CPEN_AGENT claude     # 기본 에이전트 고정 (선택 단계가 사라진다)
set -Ux CPEN_FOCUS never      # 턴 종료 포커스 끄기 (auto|always|never)
```

에이전트는 `-a` > `$CPEN_AGENT` > 대화형 선택 순으로 정해진다.
세션 이름을 생략하면 `pen:<파일명>` 이 쓰인다.

파일 탐색은 **git 루트** 기준이라 하위 디렉토리에서 실행해도 같은 목록이 나오고,
에이전트도 git 루트에서 시작한다. git 저장소가 아니면 현재 디렉토리를 쓴다.
`.pen` 을 gitignore 해두는 저장소가 있어 `--no-ignore` 로 훑되,
`build/` `.dart_tool/` `node_modules/` `Pods/` `.git/` `DerivedData/` 는 제외한다.

## 에이전트별 차이

|  | codex | claude |
|---|---|---|
| 작업 디렉토리 | `-C <git 루트>` | `env -C` (claude 에 `-C` 가 없다) |
| 세션 이름 | 프롬프트로 `/rename` 안내 (CLI 플래그 없음) | `--name` 으로 직접 지정 |

## 동시 편집 차단

같은 `.pen` 을 두 에이전트가 동시에 수정하면 Pencil 쪽 직렬화 보장이 없다.
서로 다른 파일끼리는 `filePath` 주소지정 덕에 구조적으로 안전하다.

방어는 두 겹이다.

**1. 리스 (lease)** — `cpen` 은 에이전트를 띄우기 *전에* 그 파일의 리스를 잡는다.
획득은 `mkdir` 이라 원자적이고, 리스에는 주인 pid 가 적힌다. 주인 프로세스가
사라지면 리스도 근거를 잃으므로 **stale lock 이 원리적으로 생기지 않는다**.
파일 목록에 점유 상태가 뜨고, 이미 점유된 파일을 고르면 확인을 받는다.

```
Pencil file (claude)> ▊
⚠ 는 작업 중인 세션입니다 - 훅이 설치되어 동시 편집은 차단됩니다
> design/1_sign_in.pen
  design/2_event_list.pen  ⚠ 작업 중: codex(pid 17949, "pen:이벤트 목록")
```

**2. PreToolUse 훅** — `cpen --install-hooks` 를 한 번 실행하면 `codex` 와
`claude` 양쪽 전역 설정에 가드가 등록된다(같은 명령이 [포커스 훅](#턴이-끝나면-pen-으로)도
함께 넣는다). 이후 `.pen` 을 건드리는 모든 Pencil MCP 호출이 실행 직전에 검사를 거친다.

> **codex 는 설치 후 승인이 한 번 더 필요하다.** 대화형 `codex` 를 띄우면
> `Hooks need review` 가 뜨는데, 여기서 `Trust all and continue` 를 골라야
> 훅이 동작한다. 승인 전까지는 **아무 경고 없이 그냥 무시된다** — 설정 파일에
> 적혀 있으니 걸린 줄 알고 작업하는 게 가장 위험하다. `cpen` 은 이 상태를
> 구분해서, 승인 전이면 설치할 때 알려주고 파일 선택 화면 헤더도 바꾼다.

- 이 세션에 배정되지 않은 `.pen` 이면 차단한다 — 프롬프트로 부탁하던 규칙이
  기계적으로 강제된다.
- 다른 세션이 점유한 `.pen` 이면 차단하고, 점유 세션을 알려주며 재시도·우회
  대신 사용자에게 보고하라고 지시한다.
- 리스가 없는 세션(`--resume` 등)이 `.pen` 을 열면 그 자리에서 리스를 잡는다.
  즉 `cpen` 을 거치지 않은 세션도 이후 다른 세션을 막는다.

판정은 도구 이름이 아니라 `filePath` 인자로 한다. Pencil MCP 의 도구는
`get_app_state` 만 빼고 전부 `filePath` 를 필수로 받으므로, 에이전트마다 다른
도구 이름 표기(`codex` 는 화면에 `pencil/execute` 로 보여준다)와 무관하게 같은
판정이 나온다.

차단은 `exit 2` + stderr 로 한다. `permissionDecision: "deny"` JSON 은 MCP
도구에서 무시된 전례가 있어 주 경로로 쓰지 않는다.

훅 설치는 기존 훅과 설정을 보존하고 멱등하며, 처음 손대기 전에
`<파일>.cpen-backup` 을 남긴다.

알려진 한계:

- 훅을 설치하지 않으면 감지 범위가 `cpen` 이 띄운 세션 + 리스뿐이다.
- 점유 확인에서 **그래도 진행**을 고르면 기존 리스의 토큰을 물려받아 훅도
  통과한다. 위험을 알고 고르는 의도된 탈출구다.
- `get_app_state` 는 `filePath` 를 받지 않아 Pen.app 전역 상태를 그대로 본다.
  읽기 전용이라 차단하지 않고, 프롬프트로 "그것을 판정 근거로 쓰지 말라" 고
  일러둔다.
- Cursor 등 `cpen` 밖의 다른 MCP 클라이언트가 같은 파일에 붙는 것은 막지 못한다.
- `cpen` 은 **동시에 붙지 못하게** 할 뿐이다. Pen.app 자체는 여전히 동시 편집을
  직렬화하지 않는다.

## 턴이 끝나면 Pen 으로

에이전트가 디자인을 고쳐도 화면은 터미널에 머문다. 결과를 보려면 매번 Pen.app 을
직접 찾아 올려야 하는데, 이 왕복이 디자인 작업에서는 꽤 잦다.

`cpen --install-hooks` 는 **Stop 훅**도 함께 등록한다. 턴이 끝날 때
`cpen-focus --if-touched` 가 돌아 작업 대상 `.pen` 창을 최전면으로 올린다.
`open -a Pen <파일>` 은 이미 열려 있는 그 문서 창을 앞으로 가져올 뿐이라
창이 새로 생기지 않는다.

기본값은 **그 턴에 파일을 실제로 고쳤을 때만** 올린다. 판단 근거는 가드 훅이
남긴다 — Pencil MCP 에서 문서를 바꾸는 도구는 `execute` 하나뿐이므로, 가드가
`execute` 호출을 통과시킬 때 리스에 표시를 남기고 Stop 훅이 그 표시를 소비한다.
대화만 오간 턴까지 창을 뺏으면 사용자가 원래 창으로 돌아가는 비용이 알림 가치보다
커지기 때문이다.

| `$CPEN_FOCUS` | 동작 |
|---|---|
| `auto` (기본) | 그 턴에 `.pen` 을 수정했을 때만 올린다 |
| `always` | 턴이 끝날 때마다 올린다 |
| `never` | 자동으로는 올리지 않는다 (`cpen-focus` 직접 호출은 여전히 동작) |

에이전트에게 시킬 수도 있다. `cpen` 이 넘기는 프롬프트에 `fish -c cpen-focus` 로
Pen.app 을 앞으로 가져올 수 있다는 안내가 들어 있어, 턴 도중에도 "지금 보세요" 가
가능하다. 사람이 셸에서 직접 `cpen-focus` 를 쳐도 같다.

알려진 한계:

- 리스가 없으면 표시를 남길 곳이 없어 `auto` 에서는 올라오지 않는다. 훅을 설치했다면
  가드가 첫 호출에서 리스를 잡으므로 실제로 문제가 되는 구간은 그 첫 호출 이전뿐이다.
- 점유 확인에서 **그래도 진행**을 골라 리스를 공유하게 된 두 세션은 표시도 공유한다.
  한쪽이 먼저 소비하면 다른 쪽은 그 턴에 올라오지 않는다.
- 실패해도 조용히 넘어간다. Stop 훅에서 non-zero 를 내면 `claude` 는 "종료를 막았다"
  로 읽고 턴을 이어가는데, 부가 기능이 대화 흐름을 건드려서는 안 된다.

### cpen 밖 세션은 건드리지 않는다

`Stop` 훅은 전역이라 `cpen` 과 무관한 세션에서도 **매 턴** 불린다. 그래서 판정을
`fish` 까지 끌고 가지 않고 `sh` 단계에서 끝낸다. 설정에 들어가는 명령은 이렇다.

```sh
sh -c '[ -n "$CPEN_PEN_FILE" ] && exec fish -c "cpen-focus --if-touched" || true'
```

`$CPEN_PEN_FILE` 은 `cpen` 이 에이전트 프로세스에만 `env` 로 넣어주는 변수다.
셸에 남지 않으므로 **그 변수의 유무가 곧 "`cpen` 이 띄운 세션인가"** 가 된다.
없으면 `fish` 조차 뜨지 않는다 — 실측으로 턴당 35ms 가 5ms 로 줄었다.

`|| true` 는 변수가 없을 때 `[` 가 내는 `1` 을 삼킨다. `exec` 는 판정이 끝난 `sh`
프로세스를 남겨두지 않는다. `sh -c` 로 감싼 형태라 에이전트가 명령을 셸에 통째로
넘기든 인자로 쪼개 실행하든 똑같이 동작한다.

`cpen-focus` 함수 자체도 `$CPEN_PEN_FILE` 이 없으면 빠지므로 방어는 두 겹이다.

한편 **가드(`PreToolUse`) 훅은 일부러 `cpen` 밖 세션에도 적용된다.** `.pen` 을 여는
세션이면 누가 띄웠든 리스를 잡아야 다음 세션을 막을 수 있기 때문이다. 매 턴 도는
Stop 훅과 달리 매처가 `.*[Pp]encil.*` 이라 Pencil MCP 를 부를 때만 실행된다.

## 테스트

```fish
fish test/run.fish
```

## 라이선스

[MIT](LICENSE)
