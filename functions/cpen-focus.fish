function cpen-focus --description "작업 대상 .pen 을 Pen.app 최전면으로 가져온다"
    # 두 가지 경로로 불린다.
    #   - 사람/에이전트가 직접: `cpen-focus` 또는 `cpen-focus <경로>` - 무조건 올린다
    #   - Stop 훅이: `fish -c "cpen-focus --if-touched"` - 정책에 따라 판단한다
    #
    # `open -a Pen <파일>` 은 이미 열려 있는 그 문서 창을 최전면으로 올린다.
    # 창을 새로 만들지 않으므로 몇 번을 불러도 안전하다.
    argparse if-touched -- $argv
    or return 1

    if set -q _flag_if_touched
        # 훅은 stdin 으로 payload 를 준다. 판정은 전부 환경변수로 하므로 내용은 쓰지
        # 않지만, 읽지 않고 끝내면 쓰는 쪽이 SIGPIPE 를 볼 수 있어 비워둔다.
        read -z -l _payload 2>/dev/null
    end

    set -l file $argv[1]
    test -n "$file"; or set file $CPEN_PEN_FILE

    if test -z "$file"
        # Stop 훅은 전역이라 cpen 과 무관한 세션에서도 매 턴 불린다. 조용히 빠진다.
        set -q _flag_if_touched; and return 0
        echo "cpen-focus: 대상 .pen 이 없습니다 - 경로를 넘기거나 cpen 세션 안에서 실행하세요" >&2
        return 1
    end

    if set -q _flag_if_touched
        test "$CPEN_FOCUS" = never; and return 0

        # 표시는 어느 정책이든 소비한다. 남겨두면 다음 턴에 이유 없이 또 올라온다.
        set -l touched 0
        _cpen_lease unmark "$file"; and set touched 1

        # 기본(auto)은 '수정이 있었던 턴에만'. 대화만 오간 턴까지 창을 뺏으면
        # 사용자가 원래 창으로 돌아가는 비용이 알림 가치보다 커진다.
        if test "$CPEN_FOCUS" != always; and test $touched -eq 0
            return 0
        end
    end

    if not open -a Pen "$file" 2>/dev/null
        # 훅에서 non-zero 를 내면 claude 는 'Stop 을 막았다' 로 읽고 턴을 이어간다.
        # 포커스는 부가 기능이라 실패해도 대화 흐름을 건드려서는 안 된다.
        set -q _flag_if_touched; and return 0
        echo "cpen-focus: Pen.app 으로 열지 못했습니다: $file" >&2
        return 1
    end
end
