# cpen

Pencil `.pen` 파일을 골라 Pencil 데스크톱 앱으로 열고, 그 파일을 작업 대상으로 하는
코딩 에이전트(codex / claude) 세션을 띄우는 Python 표준 라이브러리 기반 CLI.

`.pen` 은 암호화 포맷이라 일반 파일 도구로 못 읽고 Pencil MCP 를 거쳐야 한다.
그래서 "어떤 파일을 작업 중인지" 를 셸이 확인할 수 없는데, `cpen` 은 이걸
**에이전트에게 넘기는 프롬프트 계약**으로 해결한다 — 파일 대상 Pencil MCP 도구의
`filePath` 를 활성 캔버스가 아니라 절대 경로로 고정한다.

## 요구사항

| | |
|---|---|
| OS | macOS 또는 Linux |
| Python | 3.9 이상 |
| 대화형 선택 | [`fzf`](https://github.com/junegunn/fzf); `-a`, `-f`를 모두 쓰면 불필요 |
| 앱 | [Pencil](https://pencil.dev) 데스크톱 앱 |
| 에이전트 | `codex` 또는 `claude` CLI 중 최소 하나 |
| 자동 저장 | Pencil 공식 `pen` 또는 `pencil` CLI |

에이전트 쪽에 **Pencil MCP 서버가 등록되어 있어야 한다**. `cpen` 이 대신
설정해주지는 않지만, Codex 실행 전 등록값을 검사해 Cursor MCP이거나 Pen desktop
대상이 아니면 실행을 중단한다.

### Codex Pencil MCP 정책

Codex도 Claude와 동일하게 **Pen 데스크톱 앱의 내장 MCP 서버**를 사용한다. Cursor용
Pencil MCP는 절대 사용하지 않는다. macOS의 Codex 등록값은 다음과 같아야 한다.

```toml
[mcp_servers.pencil]
command = "/Applications/Pen.app/Contents/Resources/app.asar.unpacked/out/mcp-server-darwin-arm64"
args = ["--app", "desktop", "--agent", "codexCLI"]
```

Linux에서도 `--app desktop`인 비-Cursor Pencil MCP만 허용한다. 이 정책은 `cpen -a
codex`의 실행 전 검사와 테스트로 유지하며, 설정이 어긋나면 Pen 앱이나 Codex를 띄우기
전에 실패한다.

## 설치

저장소를 clone한 뒤 Python 설치 스크립트를 실행한다. 패키징 프레임워크나 외부 Python
패키지는 사용하지 않고 `~/.local/bin`에 네 명령과 공유 모듈만 복사한다.

```sh
git clone https://github.com/zerodice0/cpen.git ~/src/cpen
python3 ~/src/cpen/install.py
```

`~/.local/bin`이 `PATH`에 없다면 사용하는 셸 설정에 추가한다.

```sh
export PATH="$HOME/.local/bin:$PATH"
```

다른 prefix가 필요하면 `python3 install.py --prefix /원하는/경로`를 사용한다.
Fish는 설치되어 있지 않아도 되고 기본 셸일 필요도 없다. 기존 Fisher 설치는 Python
진입점을 설치한 뒤 `fisher remove zerodice0/cpen`으로 제거한다. Fish 사용자를 위한
`completions/*.fish`만 선택적 호환 데이터로 남아 있으며 런타임 코드를 실행하지 않는다.
필요하면 `completions/*.fish`를 `~/.config/fish/completions/`에 직접 복사할 수 있다.
이 호환 파일은 1.0에서 제거할 예정이다.

작업 완료 시 선택한 `.pen` 파일을 자동 저장하려면 훅을 한 번 설치한다.

```sh
cpen --install-hooks
```

Codex는 새 세션에서 `/hooks`를 열어 새 cpen 훅을 한 번 승인해야 한다. Claude는 다음
새 세션부터 설정을 읽는다. 훅은 `cpen`으로 시작한 세션에서만 동작한다.

## 사용법

```sh
cpen                          # 에이전트 선택 → 파일 선택
cpen -a claude                # 에이전트 선택 단계를 건너뛴다
cpen -a claude -f design/a.pen # 파일 선택 단계도 건너뛴다
cpen -a claude 이벤트 목록 개편  # 세션 이름까지 지정
cpen --help

cpen --install-hooks          # 작업 완료 시 .pen 자동 저장 훅 설치
cpen --uninstall-hooks        # cpen 훅 제거

cpen-focus                    # 대상 .pen 을 지금 앞으로 가져온다
cpen-focus design/a.pen       # 경로를 직접 지정할 수도 있다

export CPEN_AGENT=claude      # 기본 에이전트 고정 (선택 단계가 사라진다)
```

에이전트는 `-a` > `$CPEN_AGENT` > 대화형 선택 순으로 정해진다.
세션 이름을 생략하면 `pen:<파일명>` 이 쓰인다.

## Herdr Pencil Session (prototype)

`herdr-plugin.toml` 은 선택한 `.pen` 파일을 하나의 Herdr 탭으로 연다.

- 왼쪽 2/3: 기존 `cpen`으로 시작한 Codex 또는 Claude
- 오른쪽 1/3: 고해상도 브라우저 주소와 프레임 선택 목록
- 파일이 저장되어 mtime이 바뀌면 선택 프레임을 자동 갱신
- Pencil desktop socket이 2배 PNG를 반환하면 메모리에만 보관해 브라우저에 제공한다.
  preview 종료 시 HTTP 서버와 메모리 캐시를 정리한다.

Herdr 0.7.5 이상이 필요하다. Tailscale이 실행 중이면 해당 인터페이스의 임시 포트에만
서버를 열고 토큰이 포함된 URL을 표시한다. 같은 tailnet의 브라우저에서 URL을 열면
이미지를 Fit 또는 디자인 좌표 기준 100%로 볼 수 있다. 트랙패드나 터치 화면에서는
핀치로 연속 확대·축소하고 확대된 이미지를 드래그해 이동한다. 키보드 `+`/`-`로도
배율을 바꾸고 `0`은 100%, `f`는 Fit으로 돌아간다. Tailscale을 찾지 못하면 localhost
주소와 SSH 터널 명령을 대신 표시한다.

macOS와 Linux 모두 파일 URL의 기본 앱으로 `.pen` 파일을 열고, preview는 Pencil
데스크톱의 `~/.pencil/socket/pencil-desktop.sock`에 직접 연결한다. 별도 launcher를
써야 하면 `CPEN_PENCIL_APP`, 기본 위치가 아닌 desktop socket을 쓰면
`CPEN_PENCIL_SOCKET`에 각각 실행 경로와 socket 경로를 지정한다.

```sh
herdr plugin link /absolute/path/to/cpen
```

설정된 단축키나 Herdr plugin action에서 `Open Pencil Session`을 실행한 뒤 파일과
에이전트를 고른다. 우측 pane의 `미리보기 열기` 링크를 Ctrl-click하거나 함께 표시된
URL을 브라우저에 붙여 넣는다. `j/k` 또는 방향키로 프레임을 바꾸고, `r`로 다시 읽으며,
`q`로 미리보기를 종료한다. 브라우저는 선택된 프레임이 바뀌면 자동 갱신된다. 같은
방식으로 탭을 여러 개 열 수 있고 각 preview는 서로 다른 포트와 토큰을 사용한다.
이 시스템의 설정에서는 Ghostty에서 `herdr`를 실행하고 `Ctrl+P`, `p`를 차례로 누른다.
preview agent는 절대 `filePath`로 문서에 연결하므로 Pencil 창을 앞으로 가져오지 않고
현재 터미널 포커스를 유지한다.

이미 `cpen`으로 실행 중인 Codex/Claude pane에는 미리보기만 붙일 수 있다. 대상 pane에
포커스를 두고 `Toggle Pencil Preview`를 실행한다. 이 시스템에서는 `Ctrl+P`, `i`를
차례로 누른다. 대상 pane의 오른쪽 1/3이 미리보기가 되며, 작은 2x2 레이아웃에서는
선택 중인 프레임 한 줄만 표시하는 compact UI를 쓴다. 같은 단축키를 다시 누르거나
미리보기에서 `q`를 누르면 미리보기만 닫히고 기존 에이전트 세션과 레이아웃은 유지된다.
각 cpen pane별로 하나씩 독립적으로 붙일 수 있다.

`cpen`으로 시작하지 않은 일반 Herdr pane에서 같은 action을 실행하면 `.pen`
선택 popup을 열고 그 pane에 파일을 연결한 다음 미리보기를 붙인다. 연결은 cpen/Herdr
state에 pane ID 기준으로 저장되므로 Codex/Claude에서 `/clear`하거나 에이전트를
같은 pane에서 재시작해도 유지된다. Herdr 서버를 재시작하면 연결은 남지만 preview
프로세스는 종료되므로 `Toggle Pencil Preview`로 다시 연다.

`Attach Pencil File`은 현재 pane의 연결 파일을 선택하거나 교체하고, `Detach Pencil
File`은 연결과 미리보기를 함께 제거한다. pane을 닫으면 연결 기록도 자동으로
정리된다.

초기 프로토타입은 기존 `.pen` 선택만 지원한다. 새 문서 생성은 Pencil 앱에서 저장한 뒤
선택하는 흐름으로 둔다.

파일 탐색은 **git 루트** 기준이라 하위 디렉토리에서 실행해도 같은 목록이 나오고,
에이전트도 git 루트에서 시작한다. git 저장소가 아니면 현재 디렉토리를 쓴다.
`.pen` 을 gitignore 해두는 저장소가 있어 gitignore 여부와 상관없이 훑되,
`build/` `.dart_tool/` `node_modules/` `Pods/` `.git/` `DerivedData/` 는 제외한다.

## 에이전트별 차이

|  | codex | claude |
|---|---|---|
| 작업 디렉토리 | `-C <git 루트>` | Python subprocess의 `cwd` (claude 에 `-C` 가 없다) |
| 세션 이름 | 프롬프트로 `/rename` 안내 (CLI 플래그 없음) | `--name` 으로 직접 지정 |

## 동시 편집 안내

같은 `.pen` 을 여러 에이전트가 동시에 수정할 수 있다. `cpen` 은 이를 차단하지 않고,
현재 점유 세션을 감지해 파일 목록과 새 에이전트의 초기 프롬프트에 알려준다.

리스는 잠금이 아니라 **점유 표시용 기록**이다. `cpen` 은 에이전트를 띄우기 전에
최초 세션을 기록하고, 주인 프로세스가 사라지면 해당 기록을 만료시킨다.
Herdr plugin으로 연결한 pane도 같은 파일 선택 목록과 에이전트의 동시 편집 안내에
포함된다.

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
`get_app_state` 의 활성 캔버스는 Pencil 앱 전역 상태라 다른 세션이 창을 바꾸면 달라질 수
있으므로, 작업 파일 판정 근거로 사용하지 않는다.

알려진 한계:

- 점유 감지는 `cpen` 이 띄운 세션과 해당 프로세스 정보에 한정된다.
- Cursor 등 `cpen` 밖의 MCP 클라이언트나 사람이 직접 수정하는 것은 감지하지 못한다.
- 안내는 프롬프트 계약이므로 에이전트가 잘못 판단하면 변경이 서로 간섭할 수 있다.
- Pencil 앱 자체가 같은 파일의 변경을 병합하거나 직렬화해 주는 것은 아니다.

## Pencil 창 수동 포커스

에이전트가 작업한 결과를 확인할 때만 다음 명령을 직접 실행한다.

```sh
cpen-focus
cpen-focus design/a.pen
```

macOS와 Linux 모두 `pen interactive --app desktop --in <파일>`에 `save()`를 전달한다.
여러 Pencil 문서가 열려 있어도 `CPEN_PEN_FILE`의 정확한 절대 경로를 저장하며 창
포커스를 바꾸지 않는다. Pencil 데스크톱 앱이 실행 중이고 CLI 인증이 완료되어 있어야
한다. macOS 손쉬운 사용 권한은 필요하지 않다.

예전 버전이 설치한 `fish -c "cpen-save --hook"`과 `cpen-focus` Stop 훅은
`cpen --install-hooks` 실행 시 Fish를 호출하지 않는 절대 Python 진입점 명령으로
교체한다. `cpen --uninstall-hooks`는 기존 Fish 기반 cpen 훅도 함께 제거한다.
`cpen-focus --if-touched` 인자는 구버전 훅이 남아 있는 실행 중 세션이 창을 바꾸지
않도록 호환용 no-op 으로 유지한다.

## 테스트

```sh
PYTHONPYCACHEPREFIX=/tmp/cpen-pycache python3 test/run.py
```

테스트 runner와 테스트 구현은 모두 Python 표준 라이브러리만 사용하며 Fish 실행 파일을
호출하지 않는다. 두 OS의 공통 분기와 가짜 Unix socket protocol을 검증한다. macOS에서는
공식 CLI 저장과 실제 desktop socket preview도 확인했다. Linux의 실제 Pencil 데스크톱
연결은 별도 Linux 호스트에서 확인해야 한다.

## 라이선스

[MIT](LICENSE)
