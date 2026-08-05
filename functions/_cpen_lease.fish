function _cpen_lease --description "cpen 리스(파일 점유권) 관리: acquire/release/owner/list/reap"
    # 리스는 "누가 어떤 .pen 을 작업 중인가" 를 프로세스 밖에 남기는 유일한 근거다.
    # argv 스캔(_cpen_sessions)과 달리 경로에 공백이 있어도, resume 으로 되살린
    # 세션이어도 잡힌다. 대신 남는 기록이므로 stale 처리를 스스로 해야 한다 -
    # owner pid 의 생존 + 프로세스 이름으로 판정한다(아래 _cpen_lease_alive).
    set -l cmd $argv[1]
    set -e argv[1]

    switch $cmd
        case dir
            _cpen_lease_dir
        case acquire
            # acquire <abs-path> <agent> <session> <token> <owner-pid>
            _cpen_lease_acquire $argv
        case release
            # release <abs-path> <token>  - 남의 리스는 지우지 않는다
            _cpen_lease_release $argv
        case owner
            # owner <abs-path>  - 살아있는 리스 1줄(TSV) 출력, 없으면 exit 1
            _cpen_lease_owner $argv
        case list
            # 살아있는 리스 전부 TSV 로 출력
            _cpen_lease_list
        case reap
            # 죽은 리스 정리
            _cpen_lease_reap
        case '*'
            echo "_cpen_lease: 알 수 없는 하위 명령 '$cmd'" >&2
            return 2
    end
end

function _cpen_lease_dir --description "리스 저장 디렉토리 경로"
    if test -n "$CPEN_LEASE_DIR"
        echo $CPEN_LEASE_DIR
    else if test -n "$XDG_CACHE_HOME"
        echo $XDG_CACHE_HOME/cpen/leases
    else
        echo $HOME/.cache/cpen/leases
    end
end

function _cpen_lease_key --description "경로 -> 파일명 안전한 키"
    # 키를 만들기 전에 반드시 정규화한다. macOS 는 /var 가 /private/var 심볼릭 링크라
    # 같은 파일이 두 경로로 들어오는데, 정규화하지 않으면 리스가 서로를 못 본다.
    # 경로를 그대로 파일명에 쓰면 길이/슬래시 문제가 생기므로 해시한다.
    if command -q shasum
        printf '%s' (path resolve $argv[1]) | shasum -a 256 | string split -f1 ' ' | string sub -l 16
    else
        printf '%s' (path resolve $argv[1]) | sha256sum | string split -f1 ' ' | string sub -l 16
    end
end

function _cpen_lease_alive --description "리스 디렉토리가 살아있는 세션의 것인지"
    # pid 만 보면 재사용된 pid 를 붙잡는다. 프로세스 이름까지 확인해
    # "그 pid 가 여전히 리스를 만든 그 프로세스인지" 를 근거로 삼는다.
    #
    # cpen 은 에이전트를 띄우기 전에 점유 표시용 리스를 남기므로 최초 주인은 fish 다.
    # 그 셸은 에이전트가 끝날 때까지 foreground 로 붙들려 있다. 이전 버전의 훅이 만든
    # 리스와의 호환을 위해 codex/claude 프로세스도 살아있는 주인으로 인정한다.
    set -l info $argv[1]/info
    test -r $info; or return 1
    set -l fields (string split \t -- (cat $info 2>/dev/null))
    test (count $fields) -ge 5; or return 1
    set -l pid $fields[2]
    string match -qr '^\d+$' -- $pid; or return 1
    set -l comm (ps -p $pid -o comm= 2>/dev/null | string trim)
    test -n "$comm"; or return 1
    string match -qr '(^|/)(codex|claude|fish)$' -- $comm
end

function _cpen_lease_acquire --description "리스 획득: 성공 0, 이미 점유 1"
    set -l path $argv[1]
    set -l agent $argv[2]
    set -l session $argv[3]
    set -l token $argv[4]
    set -l pid $argv[5]

    set -l dir (_cpen_lease_dir)
    set -l cell $dir/(_cpen_lease_key $path).d
    mkdir -p $dir; or return 2

    # mkdir 은 원자적이라 cpen 두 개가 같은 순간에 들어와도 하나만 성공한다.
    # 이게 기존 "감지 후 실행" 사이의 TOCTOU 를 없애는 지점이다.
    if not mkdir $cell 2>/dev/null
        if _cpen_lease_alive $cell
            return 1
        end
        # 죽은 리스는 치우고 딱 한 번만 다시 시도한다. 여기서 또 실패하면
        # 다른 프로세스가 먼저 가져간 것이므로 점유로 본다.
        rm -rf $cell
        mkdir $cell 2>/dev/null; or return 1
    end

    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        (path resolve $path) $pid $agent $session $token (date +%Y-%m-%dT%H:%M:%S) >$cell/info
end

function _cpen_lease_release --description "자기 리스만 해제"
    set -l path $argv[1]
    set -l token $argv[2]
    set -l cell (_cpen_lease_dir)/(_cpen_lease_key $path).d
    test -d $cell; or return 0

    set -l fields (string split \t -- (cat $cell/info 2>/dev/null))
    # 토큰이 다르면 이미 다른 세션이 넘겨받은 리스다. 건드리지 않는다.
    if test (count $fields) -ge 5; and test "$fields[5]" != "$token"
        return 1
    end
    rm -rf $cell
end

function _cpen_lease_owner --description "살아있는 리스 1줄 출력"
    set -l cell (_cpen_lease_dir)/(_cpen_lease_key $argv[1]).d
    _cpen_lease_alive $cell; or return 1
    cat $cell/info
end

function _cpen_lease_list --description "살아있는 리스 전부 출력"
    set -l dir (_cpen_lease_dir)
    test -d $dir; or return 0
    for cell in $dir/*.d
        _cpen_lease_alive $cell; and cat $cell/info
    end
end

function _cpen_lease_reap --description "죽은 리스 정리"
    set -l dir (_cpen_lease_dir)
    test -d $dir; or return 0
    for cell in $dir/*.d
        _cpen_lease_alive $cell; or rm -rf $cell
    end
end
