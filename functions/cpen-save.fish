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

    set -l save_result (osascript \
        -e 'use framework "Foundation"' \
        -e 'on run argv' \
        -e 'set targetPath to item 1 of argv' \
        -e 'set targetURL to ((current application\'s NSURL\'s fileURLWithPath:targetPath)\'s absoluteString()) as text' \
        -e 'tell application "System Events"' \
        -e 'if not (exists application process "Pen") then error "Pen.app is not running"' \
        -e 'tell application process "Pen" to set windowNames to name of every window' \
        -e 'if windowNames does not contain targetURL then return "window-unavailable"' \
        -e 'set previousProcess to first application process whose frontmost is true' \
        -e 'try' \
        -e 'tell application process "Pen"' \
        -e 'set targetWindow to first window whose name is targetURL' \
        -e 'set frontmost to true' \
        -e 'perform action "AXRaise" of targetWindow' \
        -e 'set value of attribute "AXMain" of targetWindow to true' \
        -e 'set value of attribute "AXFocused" of targetWindow to true' \
        -e 'delay 0.1' \
        -e 'click menu item "Save" of menu "File" of menu bar 1' \
        -e 'end tell' \
        -e 'delay 0.1' \
        -e 'if name of previousProcess is not "Pen" then set frontmost of previousProcess to true' \
        -e 'on error errorMessage number errorNumber' \
        -e 'if name of previousProcess is not "Pen" then set frontmost of previousProcess to true' \
        -e 'error errorMessage number errorNumber' \
        -e 'end try' \
        -e 'end tell' \
        -e 'end run' \
        -- "$pen_file")
    set -l save_status $status
    test $save_status -eq 0; or return 1

    if set -q _flag_hook
        echo '{}'
    else if test "$save_result" = window-unavailable
        echo "cpen: 저장 건너뜀 - 화면이 잠겼거나 대상 Pen 창이 열려 있지 않습니다 ($pen_file)"
    else
        echo "cpen: 저장 완료 ($pen_file)"
    end
end
