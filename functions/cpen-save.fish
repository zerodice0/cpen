function cpen-save --description "cpen 대상 .pen 파일 저장"
    argparse h/help hook -- $argv
    or return 1

    if set -q _flag_help
        echo "사용법: cpen-save [파일.pen]"
        echo "cpen 세션에서는 CPEN_PEN_FILE을 사용하므로 파일을 생략할 수 있습니다."
        return 0
    end

    set -l pen_file $argv[1]
    test -n "$pen_file"; or set pen_file $CPEN_PEN_FILE

    if test -z "$pen_file"
        if set -q _flag_hook
            echo '{}'
            return 0
        end
        echo "cpen-save: 저장할 .pen 파일이 없습니다" >&2
        return 1
    end
    if test (count $argv) -gt 1
        echo "cpen-save: 파일은 하나만 지정하세요" >&2
        return 1
    end

    set pen_file (path resolve $pen_file)
    if not test -f "$pen_file"
        echo "cpen-save: 파일을 찾을 수 없습니다: $pen_file" >&2
        return 1
    end
    if not string match -q '*.pen' -- $pen_file
        echo "cpen-save: .pen 파일만 저장할 수 있습니다: $pen_file" >&2
        return 1
    end

    set -l pencil_cli
    if type -q pen
        set pencil_cli pen
    else if type -q pencil
        set pencil_cli pencil
    else
        echo "cpen-save: pen 또는 pencil CLI를 찾을 수 없습니다" >&2
        return 1
    end

    printf 'save()\nexit()\n' | \
        $pencil_cli interactive --app desktop --in "$pen_file" \
            >/dev/null 2>&1
    test $status -eq 0; or return 1

    if set -q _flag_hook
        echo '{}'
    else
        echo "cpen: 저장 완료 ($pen_file)"
    end
end
