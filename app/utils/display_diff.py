def diff_display(current: dict, last: dict, depth: int = 1) -> dict:
    """
    current 중 last와 값이 다른 key만 반환.

    depth=1 (기본): top-level key 단위로 통째 비교. 기존 동작과 동일.
    depth=N: N depth까지 dict 내부 필드 단위로 재귀 비교.
             dict가 아닌 값(str, list, int 등)은 해당 depth에서 통째 비교.

    last는 in-place로 current 전체로 갱신됨 (다음 비교 기준이 됨).
    """
    changed = _dict_diff(current, last, depth)
    last.clear()
    last.update(current)
    return changed


def _dict_diff(current: dict, last: dict, depth: int) -> dict:
    changed = {}
    for k, v in current.items():
        prev = last.get(k)
        if depth >= 2 and isinstance(v, dict) and isinstance(prev, dict):
            field_diff = _dict_diff(v, prev, depth - 1)
            if field_diff:
                changed[k] = field_diff
        else:
            if prev != v:
                changed[k] = v
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