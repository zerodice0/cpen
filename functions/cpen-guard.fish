function cpen-guard --description "구버전 PreToolUse 훅 호환용 no-op"
    # 구버전 설정에 훅이 남아 있어도 Pencil MCP 호출을 막지 않는다.
    # stdin payload 를 소비해 호출자를 기다리게 하지 않고 항상 통과시킨다.
    read -z -l _payload 2>/dev/null
    return 0
end
