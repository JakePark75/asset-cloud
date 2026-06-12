def diff_display(current: dict, last: dict) -> dict:
    """
    current 중 last와 값이 다른 top-level key만 반환.
    - 비교는 '화면에 표시되는 최종 포맷 문자열/값' 기준 (포맷팅은 호출 전에 끝나 있어야 함).
    - dict/str 값 모두 ==/!= 로 비교 가능 (Python dict equality는 재귀적 값 비교).
    - last는 in-place로 current 전체로 갱신됨 (다음 비교 기준이 됨).
    """
    changed = {}
    for k, v in current.items():
        if last.get(k) != v:
            changed[k] = v
    last.clear()
    last.update(current)
    return changed