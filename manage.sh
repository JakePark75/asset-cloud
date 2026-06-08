#!/bin/bash

SERVICES=("myassets" "price_updater" "daily_inserter" "nginx")
PROJECT_ROOT="/home/ubuntu/asset-cloud"

# 압축 수행 함수
compress_source() {
    local timestamp=$(TZ='Asia/Seoul' date +"%Y%m%d_%H%M")
    local filename="asset-cloud_$timestamp.tar.gz"
    local temp_path="/tmp/$filename"
    local dest_path="$PROJECT_ROOT/$filename"

    local stale=$(ls /tmp/asset-cloud_*.tar.gz 2>/dev/null)
    if [ -n "$stale" ]; then
        echo "--- /tmp 찌꺼기 발견, 삭제 중 ---"
        sudo rm -f /tmp/asset-cloud_*.tar.gz
        echo "✅ 삭제 완료"
    fi

    echo "--- 소스 전체 압축 중 (KST: $(TZ='Asia/Seoul' date '+%Y-%m-%d %H:%M:%S')) ---"
    echo "--- 대상: $PROJECT_ROOT ---"

    sudo tar -czvf "$temp_path" \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        --exclude='.pytest_cache' \
        --exclude='.mypy_cache' \
        --exclude='.ruff_cache' \
        --exclude='node_modules' \
        --exclude='.next' \
        --exclude='dist' \
        --exclude='build' \
        --exclude='*.egg-info' \
        --exclude='.venv' \
        --exclude='venv' \
        --exclude='*.log' \
        --exclude='asset-cloud_*.tar.gz' \
        -C "$(dirname "$PROJECT_ROOT")" "$(basename "$PROJECT_ROOT")"

    if [ $? -ne 0 ]; then
        echo ""
        echo "❌ 압축 실패 — 임시 파일 삭제"
        sudo rm -f "$temp_path"
        return
    fi

    sudo mv "$temp_path" "$dest_path"
    sudo chown $USER:$USER "$dest_path"

    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ 압축 완료: $dest_path"
    else
        echo ""
        echo "❌ 소유권 변경 실패"
    fi
}

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
    echo "  4. 소스 전체 압축"
    echo "  0. 종료"
    echo ""
    read -n 1 -p "선택: " choice
    echo

    case $choice in
        1) show_service_menu "restart" "true" ;;
        2) show_service_menu "status" "true" ;;
        3) show_service_menu "log" "false" ;;
        4) compress_source ;;
        0) echo "종료"; exit 0 ;;
        *) echo "❌ 잘못된 선택" ;;
    esac
done