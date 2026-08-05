function cpen-focus --description "작업 대상 .pen 을 Pencil 데스크톱 앱으로 연다"
    # 사람이 직접 `cpen-focus` 또는 `cpen-focus <경로>` 로 호출할 때만 창을 올린다.
    # `--if-touched` 는 구버전 Stop 훅이 남아 있는 실행 중 세션의 호환용 no-op 이다.
    argparse if-touched -- $argv
    or return 1

    if set -q _flag_if_touched
        # 구버전 훅이 stdin 으로 payload 를 보낼 수 있으므로 소비만 하고 끝낸다.
        read -z -l _payload 2>/dev/null
        return 0
    end

    set -l file $argv[1]
    test -n "$file"; or set file $CPEN_PEN_FILE

    if test -z "$file"
        echo "cpen-focus: 대상 .pen 이 없습니다 - 경로를 넘기거나 cpen 세션 안에서 실행하세요" >&2
        return 1
    end

    set -l open_command
    switch (uname -s)
        case Darwin
            set open_command open -a Pen
        case Linux
            if test -n "$CPEN_PENCIL_APP"
                set open_command $CPEN_PENCIL_APP
            else if command -q pen-desktop
                set open_command pen-desktop
            else
                set open_command xdg-open
            end
    end
    if test (count $open_command) -eq 0; or not $open_command "$file" 2>/dev/null
        echo "cpen-focus: Pencil 데스크톱 앱으로 열지 못했습니다: $file" >&2
        return 1
    end
end
