from shiny import ui
import json
from .history_utils import fmt_krw, fmt_10m
import time
import logging

def render_history_table(rows):
    """
    daily_summary rows (ASC) → Shiny UI 테이블 반환 (최신순 표시).
    """
    t0 = time.time()
    if not rows:
        return ui.p("데이터가 없습니다.", style="color:#888; padding:16px;")

    rows_desc = list(reversed(rows))

    # 전일 대비 계산용
    index_map = {r[0]: i for i, r in enumerate(rows)}
    asset_map = {r[0]: float(r[1] or 0) for r in rows}
    dates_asc = [r[0] for r in rows]

    def prev_asset(date):
        idx = index_map[date]
        return asset_map[dates_asc[idx - 1]] if idx > 0 else None

    header = ui.tags.tr(
        ui.tags.th("날짜"),
        ui.tags.th("총자산"),
        ui.tags.th("전일대비"),
        ui.tags.th("입출금"),
        ui.tags.th("Exp"),
        ui.tags.th("현금"),
        ui.tags.th("x1"),
        ui.tags.th("x2"),
        ui.tags.th("x3"),
    )

    trs = []
    for r in rows_desc:
        date, total, twr, ndx, cf, cf_note, exp, cash, x1, x2, x3 = r

        total_f = float(total or 0)
        cf_f    = float(cf or 0)
        exp_f   = float(exp or 0)
        cash_f  = float(cash or 0)
        x1_f    = float(x1 or 0)
        x2_f    = float(x2 or 0)
        x3_f    = float(x3 or 0)

        # 전일 대비
        prev = prev_asset(date)
        if prev is not None and prev != 0:
            diff     = total_f - prev
            diff_pct = diff / prev * 100
            sign     = "+" if diff >= 0 else ""
            cls      = "positive" if diff >= 0 else "negative"
            diff_cell = ui.tags.span(
                f"{sign}{fmt_krw(diff)}",
                ui.tags.br(),
                ui.tags.span(f"{sign}{diff_pct:.2f}%", style="font-size:11px;"),
                class_=cls,
            )
        else:
            diff_cell = ui.tags.span("-", style="color:#555;")

        # 입출금
        if cf_f != 0:
            sign   = "+" if cf_f > 0 else ""
            cf_cls = "positive" if cf_f > 0 else "negative"
            cf_str = f"{sign}{fmt_krw(cf_f)}"
            if cf_note:
                cf_cell = ui.tags.span(
                    cf_str,
                    title=cf_note,
                    class_=cf_cls,
                    style="cursor:pointer; border-bottom:1px dotted;",
                    onclick="alert({})".format(json.dumps(cf_note)),
                )
            else:
                cf_cell = ui.tags.span(cf_str, class_=cf_cls)
        else:
            cf_cell = ui.tags.span("-", style="color:#555;")

        trs.append(ui.tags.tr(
            ui.tags.td(str(date)),
            ui.tags.td(fmt_10m(total_f), style="text-align:right;"),
            ui.tags.td(diff_cell,          style="text-align:right;"),
            ui.tags.td(cf_cell,            style="text-align:right;"),
            ui.tags.td(f"{exp_f*100:.1f}%",  style="text-align:right;"),
            ui.tags.td(f"{cash_f*100:.1f}%", style="text-align:right;"),
            ui.tags.td(f"{x1_f*100:.1f}%",  style="text-align:right;"),
            ui.tags.td(f"{x2_f*100:.1f}%",  style="text-align:right;"),
            ui.tags.td(f"{x3_f*100:.1f}%",  style="text-align:right;"),
        ))

    result = ui.div(
        ui.tags.table(
            ui.tags.thead(header),
            ui.tags.tbody(*trs),
            class_="history-table",
        )
    )
    logging.warning(f"[history] render_history_table: {time.time()-t0:.3f}s")
    return result