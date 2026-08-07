# 선택적으로 Fish를 쓰는 사용자를 위한 completion 데이터다. cpen 런타임은 Python이다.
# 인자를 생략하면 $CPEN_PEN_FILE 을 쓰므로 파일 완성은 보조 수단이다.
complete -c cpen-focus -f -a "(__fish_complete_suffix .pen)"
complete -c cpen-focus -l if-touched -d "훅 전용: 수정이 있었던 턴에만 포커스"
