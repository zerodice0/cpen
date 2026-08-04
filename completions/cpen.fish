# 함수 파일은 호출 시점에야 로드되므로 completion 은 여기에 둔다.
complete -c cpen -f
complete -c cpen -s a -l agent -x -a "codex claude" -d "사용할 에이전트 (기본: \$CPEN_AGENT 또는 codex)"
complete -c cpen -s f -l file -r -a "(__fish_complete_suffix .pen)" -d ".pen 파일을 직접 지정"
complete -c cpen -l install-hooks -d "작업 완료 시 .pen 자동 저장 훅 설치"
complete -c cpen -l uninstall-hooks -d "cpen 훅 제거"
complete -c cpen -s h -l help -d "도움말"
