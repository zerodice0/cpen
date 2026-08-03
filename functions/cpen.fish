function cpen --description "Select a .pen file and start an agent (codex/claude)"
    argparse h/help a/agent= install-hooks uninstall-hooks -- $argv
    or return 1

    if set -q _flag_help
        echo "사용법: cpen [-a codex|claude] [세션 이름...]"
        echo
        echo "  .pen 파일을 골라 Pen.app 으로 열고 에이전트를 띄운다."
        echo "  에이전트는 -a > \$CPEN_AGENT > 대화형 선택 순으로 결정된다."
        echo
        echo "  -a, --agent <codex|claude>   사용할 에이전트"
        echo "      --install-hooks          동시 편집 차단 훅과 포커스 훅을 설치한다"
        echo "      --uninstall-hooks        설치한 훅을 제거한다"
        echo "  -h, --help                   이 도움말"
        echo
        echo "  세션 이름을 생략하면 pen:<파일명> 이 쓰인다."
        echo "  set -Ux CPEN_AGENT claude    로 기본 에이전트를 고정한다."
        echo "  set -Ux CPEN_FOCUS never     로 턴 종료 포커스를 끈다 (auto|always|never)."
        echo "  cpen-focus                   로 대상 파일을 직접 앞으로 가져온다."
        return 0
    end

    if set -q _flag_install_hooks
        _cpen_hooks install
        return $status
    end

    if set -q _flag_uninstall_hooks
        _cpen_hooks uninstall
        return $status
    end

    # 우선순위: -a 플래그 > $CPEN_AGENT > 대화형 선택.
    # 잘못된 $CPEN_AGENT 는 선택 UI 로 흘려보내지 않고 아래에서 오류로 잡는다.
    set -l agent $_flag_agent
    test -n "$agent"; or set agent $CPEN_AGENT

    if test -z "$agent"
        set agent (
            printf '%s\n' codex claude |
            fzf \
                --prompt="Agent> " \
                --height=~40% \
                --reverse \
                --header="고정하려면 set -Ux CPEN_AGENT <agent> 또는 cpen -a <agent>"
        )
        test -n "$agent"; or return 1
    end

    if not contains -- $agent codex claude
        echo "cpen: 알 수 없는 에이전트 '$agent' (codex|claude)" >&2
        return 1
    end

    # 탐색 기준점을 실행 기준점(git 루트)과 맞춘다.
    # 맞추지 않으면 하위 디렉토리에서 실행했을 때 목록이 비어 조용히 종료된다.
    set -l workdir (git rev-parse --show-toplevel 2>/dev/null)
    test -n "$workdir"; or set workdir (pwd)

    set -l occupants (_cpen_occupants)

    # 목록은 "정규 경로<TAB>표시문자열" 로 만들고 fzf 에는 표시문자열만 보여준다.
    # --no-ignore: .pen 을 gitignore 해두는 저장소가 있어 빼면 아예 안 잡힌다.
    # 대신 재생성 산출물을 배제해 스캔 대상을 줄인다.
    set -l rows
    for found in (
        fd --no-ignore --type f --extension pen \
            -E build -E .dart_tool -E node_modules -E Pods -E .git -E DerivedData \
            . "$workdir"
    )
        set -l abs (path resolve $found)
        set -l disp (string replace -- "$workdir/" "" $abs)
        set -l busy (_cpen_busy_label $abs $occupants)
        test -n "$busy[1]"; and set disp "$disp  ⚠ 작업 중: $busy[1]"
        set -a rows "$abs"\t"$disp"
    end

    if test (count $rows) -eq 0
        echo "cpen: .pen 파일이 없습니다: $workdir" >&2
        return 1
    end

    set -l picked (
        printf '%s\n' $rows |
        fzf \
            --prompt="Pencil file ($agent)> " \
            --height=40% \
            --reverse \
            --delimiter=\t \
            --with-nth=2.. \
            --header=(_cpen_pick_header)
    )

    test (count $picked) -eq 1; or return 1

    set -l pen_file (string split -f1 \t -- $picked)

    set -l session_label (string join " " $argv)
    if test -z "$session_label"
        set -l stem (path change-extension "" (path basename $pen_file))
        set session_label "pen:$stem"
    end

    # 같은 파일을 여러 세션이 잡으면 Pencil 쪽 동시성 보장이 없다.
    # 다른 파일끼리는 도구가 filePath 로 주소지정하므로 구조적으로 안전하다.
    #
    # 리스를 에이전트보다 *먼저* 잡는 이유: 여기가 원자적이지 않으면 cpen 두 개가
    # 같은 순간에 목록을 보고 둘 다 통과한다(예전 argv 스캔의 TOCTOU).
    # mkdir 로 잡으므로 경합해도 한쪽만 성공한다.
    set -l token (uuidgen)
    set -l holds_lease 1
    _cpen_lease acquire $pen_file $agent $session_label $token %self
    switch $status
        case 0
            # 획득 성공
        case 2
            echo "cpen: 리스 디렉토리를 만들 수 없습니다: "(_cpen_lease dir) >&2
            return 1
        case '*'
            set -l busy (_cpen_busy_label $pen_file (_cpen_occupants))
            set -l name (path basename $pen_file)
            echo "cpen: $name 을 $busy[2] 가 작업 중입니다." >&2
            read -l -P "      그래도 진행합니까? [y/N] " reply
            string match -qir '^y' -- $reply; or return 1

            # 강제로 진행하는 세션은 기존 리스의 토큰을 물려받는다.
            # 그래야 훅이 "같은 소유자" 로 보고 통과시킨다 - 사용자가 위험을 알고
            # 고른 길이므로 여기서 또 막지 않는다. 대신 리스 해제는 원 주인에게 맡긴다.
            set -l info (_cpen_lease owner $pen_file)
            and set token (string split -f5 \t -- $info)
            set holds_lease 0
    end

    if not open -a Pen "$pen_file"
        echo "cpen: Pen.app 으로 파일을 열지 못했습니다: $pen_file" >&2
        test $holds_lease -eq 1; and _cpen_lease release $pen_file $token
        return 1
    end

    # 여기서 Pen 이 열리기를 기다리지 않는다. 에이전트의 첫 MCP 호출까지는 CLI 기동 +
    # 모델 왕복으로 이미 수 초가 걸리고(실측 6.2초), 콜드 스타트라면 1~2초 대기로는
    # 어차피 부족하다. 대상 지정은 아래 filePath 계약이 맡는다.

    # Pencil MCP 의 변경/조회 도구는 모두 filePath 를 받는다. 대상 지정은 그 인자로 하고,
    # get_app_state 의 '활성 캔버스' 는 Pen.app 전역 공유라 판정 근거로 쓰지 않는다.
    set -l guard \
        "Pencil 작업 세션: $session_label" \
        "작업 대상 .pen 파일: $pen_file" \
        "Pencil MCP 도구를 호출할 때 filePath 에는 항상 위 절대 경로를 넘기세요." \
        "다른 .pen 파일은 읽지도 수정하지도 마세요." \
        "수정한 결과를 사용자가 지금 봐야 할 때는 'fish -c cpen-focus' 로 Pen.app 을 앞으로 가져오세요. 턴이 끝나면 자동으로 올라오므로 습관적으로 부를 필요는 없습니다." \
        "활성 캔버스(get_app_state)는 Pen.app 전역 공유라 다른 에이전트 세션 때문에 위 경로와 다를 수 있습니다. 그것을 이유로 멈추지 말고 filePath 로 작업하세요." \
        ".pen 파일은 Pencil MCP로만 읽고 수정하세요." \
        "다른 세션이 점유한 .pen 을 건드리면 PreToolUse 훅이 호출을 차단합니다. 차단되면 재시도하거나 우회하지 말고 사용자에게 보고하세요."

    # 훅은 이 변수들로 판정한다. 에이전트를 env 로 감싸 자식 프로세스(훅 포함)까지
    # 상속시킨다. CPEN_LEASE_DIR 과 CPEN_FOCUS 를 굳이 넘기는 건, 사용자가
    # set -U(비-export)로 지정했을 때 훅이 다른 값을 보는 사고를 막기 위해서다.
    set -l focus $CPEN_FOCUS
    test -n "$focus"; or set focus auto

    set -l penv \
        CPEN_PEN_FILE=$pen_file \
        CPEN_SESSION=$session_label \
        CPEN_LEASE_TOKEN=$token \
        CPEN_LEASE_DIR=(_cpen_lease dir) \
        CPEN_FOCUS=$focus

    switch $agent
        case codex
            # codex 에는 세션 이름 플래그가 없고 TUI 가 alt screen 을 쓰므로
            # 셸에서 출력한 안내는 화면 전환에 가려진다. 프롬프트로 전달한다.
            set -a guard "첫 응답 마지막에 '세션 이름 지정: /rename $session_label' 을 안내하세요."
            # string collect: 명령치환은 개행에서 분할하므로 없으면 한 줄로 뭉개진다
            env $penv codex -C "$workdir" (string join \n $guard | string collect)
        case claude
            # claude 는 -C 가 없어 env -C 로 cwd 를 넘긴다(함수 안 cd 는 호출자 셸로 샌다).
            # 세션 이름은 --name 으로 직접 지정되므로 /rename 안내가 필요 없다.
            env -C "$workdir" $penv claude \
                --name "$session_label" \
                (string join \n $guard | string collect)
    end
    set -l rc $status

    # 에이전트는 foreground 라 여기까지 오면 세션이 끝난 것이다.
    # (Ctrl-C 도 에이전트가 받고 종료하므로 이 줄에 도달한다)
    test $holds_lease -eq 1; and _cpen_lease release $pen_file $token
    return $rc
end

function _cpen_pick_header --description "파일 선택 화면 헤더"
    # 훅이 없으면 리스를 남기는 건 cpen 자신뿐이라, 감지 범위가 예전과 같다.
    # 그 차이를 헤더에서 분명히 해 둔다.
    if _cpen_hooks status >/dev/null
        echo "⚠ 는 작업 중인 세션입니다 - 훅이 설치되어 동시 편집은 차단됩니다"
    else
        echo "⚠ 는 cpen 으로 띄운 세션만 감지합니다 - cpen --install-hooks 로 차단까지 켜세요"
    end
end

function _cpen_occupants --description "점유 중인 세션: 정규경로<TAB>pid<TAB>agent<TAB>세션"
    set -l seen
    # 리스가 1순위 근거다 - 세션 이름까지 남고 경로에 공백이 있어도 잡힌다.
    for line in (_cpen_lease list)
        set -l f (string split \t -- $line)
        set -a seen "$f[1]"\t"$f[2]"
        printf '%s\t%s\t%s\t%s\n' $f[1] $f[2] $f[3] $f[4]
    end
    # 훅을 설치하지 않은 세션은 리스를 남기지 않으므로 argv 스캔으로 보완한다.
    for line in (_cpen_sessions)
        set -l f (string split \t -- $line)
        set -l abs (path resolve $f[3])
        contains -- "$abs"\t"$f[1]" $seen; and continue
        printf '%s\t%s\t%s\t%s\n' $abs $f[1] $f[2] ""
    end
end

function _cpen_busy_label --description "경로를 점유한 세션 요약 2줄 (1=목록용 짧게, 2=확인용 자세히). 없으면 빈 출력"
    # 빈 출력을 문자열에 인접 결합하면 fish 는 데카르트 곱으로 인자를 통째로 없앤다.
    # 호출부는 반드시 변수로 먼저 받고 test -n 으로 판정할 것.
    set -l target (path resolve $argv[1])
    set -l short
    set -l long
    for s in $argv[2..]
        # 경로<TAB>pid<TAB>agent<TAB>세션 (_cpen_occupants 형식)
        set -l p (string split \t -- $s)
        test "$p[1]" = "$target"; or continue
        set -a short "$p[3]"
        # 세션 이름은 리스가 있을 때만 있다. 없으면 pid 만으로 식별한다.
        if test -n "$p[4]"
            set -a long "$p[3]"(printf '(pid %s, "%s")' $p[2] $p[4])
        else
            set -a long "$p[3]"(printf '(pid %s)' $p[2])
        end
    end
    switch (count $long)
        case 0
        case 1
            echo $long[1]
            echo $long[1]
        case '*'
            echo "세션 "(count $long)"개"
            echo "세션 "(count $long)"개 ("(string join ", " $long)")"
    end
end

function _cpen_sessions --description "cpen 이 띄운 진행 중 세션: pid<TAB>agent<TAB>정규경로"
    # 락 파일 대신 프로세스 자체를 근거로 삼는다 - stale lock 이 원리적으로 생기지 않는다.
    # -ww 없이는 argv 가 잘려 경로가 유실된다. -U 로 좁히는 건 비용뿐 아니라 정확성 -
    # 에이전트는 항상 사용자 권한으로 뜬다.
    # 선필터가 없으면 프로세스 수천 개를 fish 정규식에 통과시켜 ps 자체보다 느려진다.
    # 한계 두 가지: 경로에 공백이 있으면 못 뽑고, `codex resume`/`claude --resume` 로
    # 되살린 세션은 argv 에 프롬프트가 없어 탐지되지 않는다.
    for line in (ps -U $USER -wwo pid=,command= 2>/dev/null | string match -r '.*\.pen')
        set -l head (string match -r '^\s*(\d+)\s+(?:\S*/)?(codex|claude)(?:\s|$)' -- $line)
        test (count $head) -eq 3; or continue
        set -l pen (string match -r '/[^ ]+\.pen' -- $line)
        test (count $pen) -eq 1; or continue
        printf '%s\t%s\t%s\n' $head[2] $head[3] $pen[1]
    end
end
