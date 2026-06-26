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


def diff_display_split(current: dict, last: dict) -> tuple[dict, dict]:
    """
    static/dynamic 구조로 분리된 current에 대해 각각 필드 단위 diff 반환.

    current 구조:
      { key: { "static": { "id": ..., ... }, "dynamic": { "id": ..., ... } } }

    반환: (dynamic_diff, static_diff)
      dynamic_diff: { key: { "id": ..., <변경된 dynamic 필드만> } }
      static_diff:  { key: { "id": ..., <변경된 static 필드만>  } }

    last는 in-place로 current 전체로 갱신됨 (다음 비교 기준).
    """
    dyn_changed = {}
    sta_changed = {}

    for k, v in current.items():
        cur_dyn = v["dynamic"]
        cur_sta = v["static"]
        prev    = last.get(k)

        if prev is None:
            # 신규 항목 — static/dynamic 전체 전송
            dyn_changed[k] = {"id": cur_dyn["id"], **cur_dyn}
            sta_changed[k] = {"id": cur_sta["id"], **cur_sta}
            continue

        prev_dyn = prev["dynamic"]
        prev_sta = prev["static"]

        dyn_diff = {fk: fv for fk, fv in cur_dyn.items() if prev_dyn.get(fk) != fv}
        sta_diff = {fk: fv for fk, fv in cur_sta.items() if prev_sta.get(fk) != fv}

        if dyn_diff:
            dyn_changed[k] = {"id": cur_dyn["id"], **dyn_diff}
        if sta_diff:
            sta_changed[k] = {"id": cur_sta["id"], **sta_diff}

    last.clear()
    last.update(current)
    return dyn_changed, sta_changed