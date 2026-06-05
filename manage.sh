#!/bin/bash

SERVICES=("myassets" "price_updater" "daily_inserter" "nginx")

show_service_menu() {
    local action=$1
    local include_all=$2
    echo ""
    echo "서비스 선택:"
    for i in "${!SERVICES[@]}"; do
        echo "  $((i+1)). ${SERVICES[$i]}"
    done
    if [ "$include_all" = "true" ]; then
        echo "  $((${#SERVICES[@]}+1)). 전체"
    fi
    echo "  0. 뒤로"
    echo ""
    read -n 1 -p "선택: " svc_choice
echo

    if [ "$svc_choice" = "0" ]; then
        return
    fi

    if [ "$include_all" = "true" ] && [ "$svc_choice" = "$((${#SERVICES[@]}+1))" ]; then
        for svc in "${SERVICES[@]}"; do
            echo "--- $svc ---"
            sudo systemctl $action $svc
        done
        echo ""
        echo "✅ 전체 완료"
    elif [ "$svc_choice" -ge 1 ] && [ "$svc_choice" -le "${#SERVICES[@]}" ] 2>/dev/null; then
        local svc="${SERVICES[$((svc_choice-1))]}"
        if [ "$action" = "log" ]; then
            echo "--- $svc 로그 (Ctrl+C로 종료) ---"
            sudo journalctl -u $svc -f
        else
            sudo systemctl $action $svc
            echo ""
            echo "✅ $svc $action 완료"
        fi
    else
        echo "❌ 잘못된 선택"
    fi
}

while true; do
    echo ""
    echo "================================"
    echo "   서버 관리"
    echo "================================"
    echo "  1. 재시작"
    echo "  2. 상태 확인"
    echo "  3. 로그 보기"
    echo "  0. 종료"
    echo ""
    read -n 1 -p "선택: " choice
echo

    case $choice in
        1) show_service_menu "restart" "true" ;;
        2) show_service_menu "status" "true" ;;
        3) show_service_menu "log" "false" ;;
        0) echo "종료"; exit 0 ;;
        *) echo "❌ 잘못된 선택" ;;
    esac
done