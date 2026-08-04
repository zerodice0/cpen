# cpen

Pencil `.pen` 파일을 골라 Pen.app 으로 열고, 그 파일을 작업 대상으로 하는
코딩 에이전트(codex / claude) 세션을 띄우는 fish 함수.

`.pen` 은 암호화 포맷이라 일반 파일 도구로 못 읽고 Pencil MCP 를 거쳐야 한다.
그래서 "어떤 파일을 작업 중인지" 를 셸이 확인할 수 없는데, `cpen` 은 이걸
**에이전트에게 넘기는 프롬프트 계약**으로 해결한다 — 파일 대상 Pencil MCP 도구의
`filePath` 를 활성 캔버스가 아니라 절대 경로로 고정한다.

## 요구사항

| | |
|---|---|
| OS | macOS (`open -a`, BSD `ps` 에 의존) |
| fish | 3.5 이상 (`path` 내장 사용) |
| 필수 명령 | [`fd`](https://github.com/sharkdp/fd), [`fzf`](https://github.com/junegunn/fzf) |
| 앱 | [Pencil](https://pencil.dev) (`Pen.app`) |
| 에이전트 | `codex` 또는 `claude` CLI 중 최소 하나 |
| 훅 설치용 | `python3`, macOS 손쉬운 사용 권한 (`osascript`) |

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

작업 완료 시 선택한 `.pen` 파일을 자동 저장하려면 훅을 한 번 설치한다.

```fish
cpen --install-hooks
```

Codex는 새 세션에서 `/hooks`를 열어 새 cpen 훅을 한 번 승인해야 한다. Claude는 다음
새 세션부터 설정을 읽는다. 훅은 `cpen`으로 시작한 세션에서만 동작한다.

## 사용법

```fish
cpen                          # 에이전트 선택 → 파일 선택
cpen -a claude                # 에이전트 선택 단계를 건너뛴다
cpen -a claude 이벤트 목록 개편  # 세션 이름까지 지정
cpen --help

cpen --install-hooks          # 작업 완료 시 .pen 자동 저장 훅 설치
cpen --uninstall-hooks        # cpen 훅 제거

cpen-focus                    # 대상 .pen 을 지금 앞으로 가져온다
cpen-focus design/a.pen       # 경로를 직접 지정할 수도 있다

set -Ux CPEN_AGENT claude     # 기본 에이전트 고정 (선택 단계가 사라진다)
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

## 동시 편집 안내

같은 `.pen` 을 여러 에이전트가 동시에 수정할 수 있다. `cpen` 은 이를 차단하지 않고,
현재 점유 세션을 감지해 파일 목록과 새 에이전트의 초기 프롬프트에 알려준다.

리스는 잠금이 아니라 **점유 표시용 기록**이다. `cpen` 은 에이전트를 띄우기 전에
최초 세션을 기록하고, 주인 프로세스가 사라지면 해당 기록을 만료시킨다.

```
Pencil file (claude)> ▊
⚠ 는 다른 세션이 작업 중입니다 - 차단하지 않고 동시 작업 안내를 전달합니다
> design/1_sign_in.pen
  design/2_event_list.pen  ⚠ 작업 중: codex(pid 17949, "pen:이벤트 목록")
```

점유 파일을 선택해도 확인 질문이나 쓰기 금지 훅 없이 바로 시작한다. 초기 프롬프트는
에이전트에게 다음을 전달한다.

- 수정 직전에 대상 노드를 다시 읽는다.
- 기억한 상태와 다르면 최신 상태를 기준으로 작업한다.
- 다른 세션의 변경을 덮어쓰거나 되돌리지 않는다.
- 한 번의 `execute` 범위를 작게 유지한다.
- 충돌이 의심되면 반복 수정하지 않고 사용자에게 현재 상태를 보고한다.

Pencil MCP 호출 대상은 활성 창이 아니라 절대 `filePath` 로 고정한다.
`get_app_state` 의 활성 캔버스는 Pen.app 전역 상태라 다른 세션이 창을 바꾸면 달라질 수
있으므로, 작업 파일 판정 근거로 사용하지 않는다.

알려진 한계:

- 점유 감지는 `cpen` 이 띄운 세션과 해당 프로세스 정보에 한정된다.
- Cursor 등 `cpen` 밖의 MCP 클라이언트나 사람이 직접 수정하는 것은 감지하지 못한다.
- 안내는 프롬프트 계약이므로 에이전트가 잘못 판단하면 변경이 서로 간섭할 수 있다.
- Pen.app 자체가 같은 파일의 변경을 병합하거나 직렬화해 주는 것은 아니다.

## Pen 창 수동 포커스

에이전트가 작업한 결과를 확인할 때만 다음 명령을 직접 실행한다.

```fish
cpen-focus
cpen-focus design/a.pen
```

자동 저장 훅은 응답이 끝날 때 대상 창을 잠깐 활성화해 저장하고, 직전에 사용하던 앱으로
포커스를 즉시 돌려놓는다. 여러 Pen 창이 열려 있어도 `CPEN_PEN_FILE`의 정확한 파일 URL로
대상 창을 고른다. 최초 실행에서 macOS가 손쉬운 사용 권한을 요청할 수 있다.

예전 버전이 설치한 `cpen-focus` Stop 훅은 `cpen --install-hooks` 또는
`cpen --uninstall-hooks` 실행 시 제거한다.
`cpen-focus --if-touched` 인자는 구버전 훅이 남아 있는 실행 중 세션이 창을 바꾸지
않도록 호환용 no-op 으로 유지한다.

## 테스트

```fish
fish test/run.fish
```

## 라이선스

[MIT](LICENSE)
