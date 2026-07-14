"""
가치평가(투자검토) 화면 모듈.

- 대상: fmp_symbols.is_active = true 인 미국 빅테크 종목 (현재 10개)
- 데이터: fmp_metrics 최신 스냅샷(하루 1회, 08:50 KST 배치 갱신) — 실시간 시세 신호(price_signal)와
  무관하므로 portfolio.py/history.py처럼 skeleton/tick diff-patch 구조를 쓰지 않는다.
- 점수(퍼센타일/컴포지트)는 저장하지 않고 화면 조회 시점에 app.utils.fmp_valuation의
  순수 함수로 계산한다 (진행 요약 문서 "확정된 설계 원칙" 참고).
- NDX 대비 초과수익률은 fmp_price_history를 조회 시점에 동적으로 읽어 계산한다
  (배치로 고정 기간 저장하지 않음, 문서 확정 사항). 기간(개월수)은 화면 전체에서
  공유하는 전역 컨트롤 하나로만 관리한다 — 메인 테이블 컬럼과 상세뷰가 같은 값을 그대로 재사용.
- 메인 테이블은 지표=행 / 종목=열 구성(전치). 종목 컬럼은 composite_score 내림차순.
  지표 설명 팝업은 고정 텍스트라 서버 호출 없이 클라이언트에서만 처리한다.

※ 이 파일은 "화면 뼈대" 단계 산출물이다. 캐싱은 최소한으로만 적용했고(세션당 1회 DB 조회),
   레이아웃/표시 항목은 사용자 피드백을 받으며 반복 수정될 것을 전제로 작성했다.
"""

from datetime import date, timedelta

import psycopg2.extras
from shiny import module, reactive, ui

from app.db import get_db
from app.utils.fmp_valuation import (
    build_axis_scores,
    excess_return_vs_benchmark,
    pe_run_rate_divergence,
    percentile_map,
    VALUTATION_AXIS_WEIGHTS,
    GROWTH_AXIS_WEIGHTS,
    QUALITY_AXIS_WEIGHTS,
    RISK_AXIS_WEIGHTS,
    OVERALL_AXIS_WEIGHTS,
)


# ── 표시 지표 정의 ────────────────────────────────────────────────────────────
# (컬럼키, 라벨, higher_is_better)
TABLE_COLUMNS = [
    ("trailing_pe",      "Trailing PE", False),
    ("forward_pe",       "Forward PE",  False),
    ("run_rate_pe",      "Run-rate PE", False),
    ("ev_fcf",           "EV/FCF",      False),
    ("psr",              "PSR",         False),
    ("revenue_cagr_3y",  "매출 CAGR 3y", True),
    ("eps_cagr_3y",      "EPS CAGR 3y",  True),
    ("fcf_cagr_3y",      "FCF CAGR 3y",  True),
    ("gross_margin",     "매출총이익률",  True),
    ("operating_margin", "영업이익률",    True),
    ("fcf_margin",       "FCF 마진",     True),
    ("roe",              "ROE",         True),
    ("debt_equity",      "부채비율",     False),
    ("net_debt_equity",  "순부채비율",   False),
]

# 상세뷰에만 노출하는 보조 지표 (fmp_metrics 원천, NDX 초과수익률은 별도 섹션에서 표시)
DETAIL_ONLY_COLUMNS = [
    ("peg",              "PEG"),
    ("ev_ebitda",        "EV/EBITDA"),
    ("revenue_cagr_5y",  "매출 CAGR 5y"),
    ("eps_cagr_5y",      "EPS CAGR 5y"),
    ("fcf_cagr_5y",      "FCF CAGR 5y"),
    ("net_margin",       "순이익률"),
    ("fcf_efficiency",   "FCF 효율(FCF/순이익)"),
]

# 메인 테이블에서 heatmap 계산에 함께 포함되는 동적 컬럼(fmp_metrics에 없음, fmp_price_history로 매번 계산)
EXTRA_TABLE_COLUMNS = [
    ("ndx_excess_return", "NDX 초과수익률", True),
]
ALL_TABLE_COLUMNS = TABLE_COLUMNS + EXTRA_TABLE_COLUMNS

# 진단용 참고 지표: 좋다/나쁘다(higher_is_better)가 성립하지 않아 percentile 히트맵 대상에서 제외.
# 절댓값이 클수록 "확인이 필요하다"는 경고색만 별도로 칠한다 (_divergence_color 참고).
DIAGNOSTIC_COLUMNS = [
    ("pe_divergence", "PE 괴리율(RR÷TTM)"),
]

# ── v1(메인 테이블) 행 "순서"만 관리하는 배열 ──────────────────────────────
# 이 리스트 안의 순서를 바꾸면 화면에 표시되는 행 순서가 그대로 바뀐다.
# (지표 자체를 추가/삭제하려면 TABLE_COLUMNS / EXTRA_TABLE_COLUMNS / DIAGNOSTIC_COLUMNS를 수정할 것 —
#  여기서는 오직 "순서"만 정한다.)
V1_ROW_ORDER = [
    "ndx_excess_return",
    "pe_divergence",    
    "trailing_pe",
    "forward_pe",
    "run_rate_pe",
    "ev_fcf",
    "psr",
    "revenue_cagr_3y",
    "eps_cagr_3y",
    "fcf_cagr_3y",
    "gross_margin",
    "operating_margin",
    "fcf_margin",
    "roe",
    "debt_equity",
    "net_debt_equity",
]

# 안전장치: TABLE_COLUMNS/EXTRA_TABLE_COLUMNS/DIAGNOSTIC_COLUMNS에는 있는데
# V1_ROW_ORDER에 순서가 빠진 지표가 있으면(추가 시 깜빡 누락 등) 조용히 화면에서
# 사라지는 대신 여기서 바로 에러로 알려준다.
_V1_KNOWN_KEYS = {key for key, *_ in TABLE_COLUMNS + EXTRA_TABLE_COLUMNS} | {key for key, _ in DIAGNOSTIC_COLUMNS}
_V1_MISSING = _V1_KNOWN_KEYS - set(V1_ROW_ORDER)
if _V1_MISSING:
    raise ValueError(f"V1_ROW_ORDER에 순서가 정의되지 않은 지표: {sorted(_V1_MISSING)}")
_V1_UNKNOWN = set(V1_ROW_ORDER) - _V1_KNOWN_KEYS
if _V1_UNKNOWN:
    raise ValueError(f"V1_ROW_ORDER에 존재하지 않는 지표 키가 있음: {sorted(_V1_UNKNOWN)}")

# 지표명 클릭 시 뜨는 설명 팝업 텍스트 (고정 텍스트, 서버 호출 없이 클라이언트에서만 사용)
METRIC_DESCRIPTIONS = {
    "trailing_pe":      "주가 ÷ 최근 4개 분기(TTM) 희석 EPS 합계. 최근 12개월 실적 기준 밸류에이션.",
    "forward_pe":       "주가 ÷ 다음 회계연도 애널리스트 컨센서스 EPS. 앞으로의 예상 실적 기준 밸류에이션.",
    "run_rate_pe":      "주가 ÷ (최신 분기 EPS × 4). 가장 최근 분기 실적만 연환산한 값이라 최근 추세를 가장 민감하게 반영.",
    "ev_fcf":           "Enterprise Value ÷ TTM 잉여현금흐름(FCF). 부채까지 포함한 기업가치 대비 실제 현금창출력.",
    "revenue_cagr_3y":  "최근 확정 연간 매출 기준, 3년 전 대비 연평균 성장률(CAGR).",
    "eps_cagr_3y":      "최근 확정 연간 EPS 기준, 3년 전 대비 연평균 성장률(CAGR). 3년 전 EPS가 적자였다면 계산 불가(NULL).",
    "fcf_cagr_3y":      "최근 확정 연간 잉여현금흐름 기준, 3년 전 대비 연평균 성장률(CAGR).",
    "gross_margin":     "매출총이익 ÷ 매출. 매출원가를 제외하고 남는 비율.",
    "operating_margin": "영업이익 ÷ 매출. 판관비까지 제외한 본업 수익성.",
    "fcf_margin":       "TTM 잉여현금흐름 ÷ TTM 매출. 매출이 실제 현금으로 얼마나 전환되는지.",
    "roe":              "순이익 ÷ 자기자본. 주주 자본 대비 수익성.",
    "debt_equity":      "총부채 ÷ 자기자본. 레버리지 수준.",
    "net_debt_equity":  "(총부채 − 현금성자산) ÷ 자기자본. 보유 현금을 제외한 실질 순부채 부담.",
    "psr":              "시가총액 ÷ TTM 매출(Price-to-Sales). 아직 이익이 안정적이지 않은 성장 기업도 비교할 수 있는 밸류에이션 지표.",
    "ndx_excess_return": "선택한 기간(개월) 동안의 종목 수익률에서 같은 기간 NDX(나스닥100) 수익률을 뺀 값. "
                          "베타(위험 조정)를 감안하지 않은 단순 상대성과이며, 지수 대비 아웃퍼폼/언더퍼폼 여부만 보여줍니다.",
    "pe_divergence":    "Run-rate PE(최근 1개 분기 실적을 연환산)가 Trailing PE(최근 4개 분기 합산, TTM) 대비 얼마나 괴리돼 "
                          "있는지(%). 음수면 최근 분기 실적이 TTM 평균보다 강해진 것(가속), 양수면 약해진 것(둔화)을 뜻합니다. "
                          "괴리가 크다는 것 자체는 좋고 나쁨을 의미하지 않습니다 — 구조적 실적 개선(레벨업)일 수도, "
                          "일시적 업황 정점(사이클 고점)일 수도 있어 방향을 직접 확인해야 하는 진단용 참고 지표입니다. "
                          "Composite 점수에는 반영되지 않습니다.",
}

DEFAULT_NDX_MONTHS = 12

_AXIS_LABELS = {"valuation": "Valuation", "growth": "Growth", "quality": "Quality", "risk": "Risk"}
_AXIS_WEIGHT_LISTS = {
    "valuation": VALUTATION_AXIS_WEIGHTS,
    "growth": GROWTH_AXIS_WEIGHTS,
    "quality": QUALITY_AXIS_WEIGHTS,
    "risk": RISK_AXIS_WEIGHTS,
}


def _metric_weight_info(key: str) -> dict | None:
    """지표가 속한 축 이름 + 축 내 가중치(%) + 전체 Composite 기준 실질 가중치(%).
    어느 축에도 없는 지표(run_rate_pe, ndx_excess_return)는 None — Composite에 반영 안 되는 참고용."""
    for axis_name, weight_list in _AXIS_WEIGHT_LISTS.items():
        for metric_key, axis_weight, _lower_is_better in weight_list:
            if metric_key == key:
                overall_axis_weight = OVERALL_AXIS_WEIGHTS.get(axis_name, 0.0)
                overall_weight = axis_weight / 100.0 * overall_axis_weight
                return {
                    "axis": _AXIS_LABELS[axis_name],
                    "axis_weight": axis_weight,
                    "overall_weight": round(overall_weight, 2),
                }
    return None


# ── DAL ───────────────────────────────────────────────────────────────────────

def _load_latest_metrics(conn) -> list[dict]:
    """fmp_metrics 최신 스냅샷 — is_active=true 종목만, 종목당 최신 calculated_at 1행."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT DISTINCT ON (m.symbol)
            m.symbol, s.name AS company_name, m.calculated_at,
            m.price, m.market_cap, m.enterprise_value,
            m.trailing_pe, m.forward_pe, m.run_rate_pe, m.peg, m.psr,
            m.ev_ebitda, m.ev_fcf,
            m.revenue_growth, m.eps_growth,
            m.revenue_cagr_3y, m.revenue_cagr_5y,
            m.eps_cagr_3y, m.eps_cagr_5y,
            m.fcf_cagr_3y, m.fcf_cagr_5y,
            m.gross_margin, m.operating_margin, m.net_margin, m.fcf_margin,
            m.roe, m.debt_equity, m.fcf_efficiency, m.net_debt_equity
        FROM fmp_metrics m
        JOIN fmp_symbols s ON s.symbol = m.symbol
        WHERE s.is_active = true
        ORDER BY m.symbol, m.calculated_at DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def _closest_closes_batch(conn, symbols: list[str], as_of: date) -> dict[str, float]:
    """symbols(NDX 포함 가능) 각각의 as_of 이전(포함) 가장 가까운 거래일 종가를 한 번의 쿼리로 배치 조회."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (symbol) symbol, close_price
        FROM fmp_price_history
        WHERE symbol = ANY(%s) AND date <= %s
        ORDER BY symbol, date DESC
    """, (symbols, as_of))
    rows = cur.fetchall()
    cur.close()
    return {sym: float(price) for sym, price in rows}


# ── 표시 헬퍼 ──────────────────────────────────────────────────────────────────

PERCENT_METRICS = {
    "revenue_cagr_3y", "eps_cagr_3y", "fcf_cagr_3y",
    "revenue_cagr_5y", "eps_cagr_5y", "fcf_cagr_5y",
    "gross_margin", "operating_margin", "net_margin", "fcf_margin",
    "roe", "revenue_growth", "eps_growth", "ndx_excess_return",
}
RATIO_METRICS = {"debt_equity", "net_debt_equity", "fcf_efficiency", "peg"}


def _fmt_metric(key: str, value) -> str:
    if value is None:
        return "-"
    v = float(value)
    if key in PERCENT_METRICS:
        return f"{v * 100:.1f}%"
    if key in RATIO_METRICS:
        return f"{v:.2f}"
    return f"{v:.1f}x"


def _fmt_score(value) -> str:
    return f"{value:.1f}" if value is not None else "-"


def _fmt_divergence(value) -> str:
    """pe_divergence는 이미 %p 단위 값(예: 12.3 = +12.3%p)이므로 PERCENT_METRICS처럼 100을 곱하지 않는다."""
    if value is None:
        return "-"
    v = float(value)
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%p"


def _divergence_color(value) -> str:
    """pe_divergence 전용 색상 — 좋다/나쁘다가 아니라 '괴리가 크니 방향을 직접 확인하라'는 경고 신호.
    부호와 무관하게 절댓값이 클수록 진한 경고색(주황 계열)을 칠한다. 완전 임시 팔레트."""
    if value is None:
        return "transparent"
    magnitude = min(abs(float(value)), 100.0)
    ratio = magnitude / 100.0
    r, g, b = 255, int(210 - ratio * 140), 60
    alpha = 0.10 + ratio * 0.35
    return f"rgba({r},{g},{b},{alpha:.2f})"


def _heatmap_color(score) -> str:
    """percentile score(0~100, 항상 높을수록 좋음 방향으로 정규화된 값) -> 배경색.
    낮음(빨강) -> 중간(노랑) -> 높음(초록). 완전 임시 팔레트 — 화면 확정 단계에서 조정 예정."""
    if score is None:
        return "transparent"
    s = max(0.0, min(100.0, float(score)))
    if s <= 50:
        ratio = s / 50
        r, g, b = 210, int(90 + ratio * 110), 90
    else:
        ratio = (s - 50) / 50
        r, g, b = int(210 - ratio * 130), 200, 90
    return f"rgba({r},{g},{b},0.32)"


def _build_table_html(records: list[dict], heatmap: dict[str, dict]) -> str:
    """지표=행 / 종목=열 (전치). records는 이미 composite_score 내림차순으로 정렬돼 있어야 함."""
    header_cells = ['<th class="val-corner">지표</th>']
    for r in records:
        symbol = r["symbol"]
        composite = r.get("composite_score")
        header_cells.append(
            f'<th class="val-symbol-col" onclick="valSymbolClick(\'{symbol}\')">'
            f'<div class="val-th-symbol">{symbol}</div>'
            f'<div class="val-th-composite">{_fmt_score(composite)}</div>'
            f'</th>'
        )
    header_row = "<tr>" + "".join(header_cells) + "</tr>"

    _heatmap_lookup = {key: (label, hib) for key, label, hib in ALL_TABLE_COLUMNS}
    _diagnostic_lookup = {key: label for key, label in DIAGNOSTIC_COLUMNS}

    body_rows = []
    for key in V1_ROW_ORDER:
        if key in _heatmap_lookup:
            label, _hib = _heatmap_lookup[key]
            weight_info = _metric_weight_info(key)
            weight_text = f"{weight_info['overall_weight']:.1f}%" if weight_info else "참고용"
            cells = [
                f'<td class="val-metric-label" onclick="valShowMetricInfo(\'{key}\')">'
                f'<div class="val-metric-label-line">{label} '
                f'<span class="val-metric-weight">{weight_text}</span></div>'
                f'</td>'
            ]
            for r in records:
                symbol = r["symbol"]
                score = heatmap[key].get(symbol)
                color = _heatmap_color(score)
                cells.append(f'<td style="background-color:{color};">{_fmt_metric(key, r.get(key))}</td>')
            body_rows.append("<tr>" + "".join(cells) + "</tr>")
        else:
            # 진단용 참고 지표(pe_divergence 등): 좋음/나쁨 히트맵이 아니라 절댓값 기준 경고색
            label = _diagnostic_lookup[key]
            cells = [
                f'<td class="val-metric-label" onclick="valShowMetricInfo(\'{key}\')">'
                f'<div class="val-metric-label-line">{label} '
                f'<span class="val-metric-weight">참고용</span></div>'
                f'</td>'
            ]
            for r in records:
                value = r.get(key)
                color = _divergence_color(value)
                cells.append(f'<td style="background-color:{color};">{_fmt_divergence(value)}</td>')
            body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<div class="valuation-table-wrap">'
        '<table class="valuation-table">'
        f'<thead>{header_row}</thead>'
        '<tbody>' + "".join(body_rows) + '</tbody>'
        '</table>'
        '</div>'
    )


def _build_detail_html(record: dict, ndx_months: int) -> str:
    axis_rows = "".join(
        f'<div class="val-axis-item"><span>{label}</span><b>{_fmt_score(record.get(key))}</b></div>'
        for key, label in [
            ("valuation_score", "Valuation"),
            ("growth_score", "Growth"),
            ("quality_score", "Quality"),
            ("risk_score", "Risk"),
        ]
    )

    main_rows = "".join(
        f'<div class="val-metric-item"><span>{label}</span><b>{_fmt_metric(key, record.get(key))}</b></div>'
        for key, label, _ in TABLE_COLUMNS
    )
    aux_rows = "".join(
        f'<div class="val-metric-item"><span>{label}</span><b>{_fmt_metric(key, record.get(key))}</b></div>'
        for key, label in DETAIL_ONLY_COLUMNS
    )

    ndx_value = record.get("ndx_excess_return")
    if ndx_value is None:
        ndx_html = '<div class="val-ndx-result">계산할 수 없습니다 (가격 데이터 부족).</div>'
    else:
        sign = "+" if ndx_value >= 0 else ""
        css = "val-positive" if ndx_value >= 0 else "val-negative"
        ndx_html = f'<div class="val-ndx-result {css}">{sign}{ndx_value * 100:.2f}%p (최근 {ndx_months}개월, {record["symbol"]} 수익률 - NDX 수익률)</div>'

    divergence_value = record.get("pe_divergence")
    divergence_color = _divergence_color(divergence_value)
    divergence_html = (
        f'<div class="val-ndx-result" style="background-color:{divergence_color};">'
        f'{_fmt_divergence(divergence_value)} (Run-rate PE ÷ Trailing PE − 1. 좋음/나쁨이 아니라 '
        f'괴리가 클수록 방향 확인이 필요하다는 신호)</div>'
    )

    return (
        f'<div class="val-detail-header">'
        f'<div class="val-detail-symbol">{record["symbol"]}</div>'
        f'<div class="val-detail-name">{record.get("company_name") or ""}</div>'
        f'<div class="val-detail-composite">Composite {_fmt_score(record.get("composite_score"))}</div>'
        f'</div>'
        f'<div class="val-axis-grid">{axis_rows}</div>'
        f'<h4 class="val-section-heading">핵심 지표</h4>'
        f'<div class="val-metric-grid">{main_rows}</div>'
        f'<h4 class="val-section-heading">보조 지표</h4>'
        f'<div class="val-metric-grid">{aux_rows}</div>'
        f'<h4 class="val-section-heading">NDX 대비 초과수익률</h4>'
        f'{ndx_html}'
        f'<h4 class="val-section-heading">PE 괴리율 (진단용 참고 지표)</h4>'
        f'{divergence_html}'
    )


def _metric_info_payload() -> dict:
    """지표명 클릭 팝업용 고정 텍스트. 서버 재호출 없이 클라이언트에서 그대로 사용."""
    payload = {}
    for key, label, _ in ALL_TABLE_COLUMNS:
        desc = METRIC_DESCRIPTIONS.get(key, "")
        weight_info = _metric_weight_info(key)
        if weight_info:
            desc += (
                f" [{weight_info['axis']} 축 내 가중치 {weight_info['axis_weight']:.0f}%, "
                f"전체 Composite 기준 약 {weight_info['overall_weight']:.1f}%]"
            )
        else:
            desc += " [Composite 점수에는 반영되지 않는 참고용 지표입니다.]"
        payload[key] = {"label": label, "desc": desc}

    for key, label in DIAGNOSTIC_COLUMNS:
        payload[key] = {"label": label, "desc": METRIC_DESCRIPTIONS.get(key, "")}

    return payload


# ── UI ────────────────────────────────────────────────────────────────────────

@module.ui
def valuation_ui():
    return ui.div(
        ui.tags.script("""
(function() {

  // ── val_table_init: 테이블 골격 렌더 (기간 변경 시 / DB 갱신 시 재전송) ──
  Shiny.addCustomMessageHandler('val_table_init', function(m) {
    window._valSymbolClickedId = m.symbol_clicked_id;
    window._valMetricInfo = m.metric_info;
    var el = document.getElementById('val-table-wrap');
    if (el) el.innerHTML = m.table_html;
  });

  // ── 종목(열 헤더) 클릭 → 상세뷰 전환 ──────────────────────────────────────
  window.valSymbolClick = function(symbol) {
    if (!window._valSymbolClickedId) return;
    Shiny.setInputValue(window._valSymbolClickedId, { symbol: symbol }, { priority: 'event' });
  };

  window.valBackToTable = function() {
    if (!window._valSymbolClickedId) return;
    Shiny.setInputValue(window._valSymbolClickedId, { symbol: null }, { priority: 'event' });
  };

  Shiny.addCustomMessageHandler('val_detail_show', function(m) {
    var tableView  = document.getElementById('val-table-view');
    var detailView = document.getElementById('val-detail-view');
    var body       = document.getElementById('val-detail-body');
    if (!tableView || !detailView || !body) return;
    tableView.style.display = 'none';
    detailView.style.display = 'block';
    body.innerHTML = m.detail_html;
  });

  Shiny.addCustomMessageHandler('val_detail_hide', function(m) {
    var tableView  = document.getElementById('val-table-view');
    var detailView = document.getElementById('val-detail-view');
    if (!tableView || !detailView) return;
    detailView.style.display = 'none';
    tableView.style.display = 'block';
  });

  // ── 지표명 클릭 → 설명 팝업 (서버 호출 없이 고정 텍스트만 표시) ────────────
  window.valShowMetricInfo = function(key) {
    var info = window._valMetricInfo && window._valMetricInfo[key];
    if (!info) return;
    var modal = document.getElementById('val-info-modal');
    var title = document.getElementById('val-info-title');
    var desc  = document.getElementById('val-info-desc');
    if (!modal || !title || !desc) return;
    title.textContent = info.label;
    desc.textContent = info.desc;
    modal.style.display = 'flex';
  };

  window.valCloseMetricInfo = function() {
    var modal = document.getElementById('val-info-modal');
    if (modal) modal.style.display = 'none';
  };

})();
        """),

        ui.div(
            {"class": "page-inner"},

            # ── 전역 컨트롤: NDX 초과수익률 기간(개월) — 테이블/상세뷰 공용 ──
            ui.div(
                {"class": "val-ndx-months-row"},
                ui.input_numeric("ndx_months", "NDX 대비 초과수익률 기간(개월)", value=DEFAULT_NDX_MONTHS, min=1, max=360),
            ),

            # ── 테이블 뷰 (지표=행 / 종목=열) ────────────────────────────
            ui.div(
                {"id": "val-table-view"},
                ui.div({"id": "val-table-wrap"}),
            ),

            # ── 상세 뷰 (기본 숨김) ──────────────────────────────────────
            ui.div(
                {"id": "val-detail-view", "style": "display:none;"},
                ui.tags.button("← 목록으로", class_="val-back-btn", onclick="valBackToTable()"),
                ui.div({"id": "val-detail-body"}),
            ),

            # ── 지표 설명 팝업 (고정 텍스트, 클라이언트 전용) ─────────────
            ui.div(
                {"id": "val-info-modal", "class": "val-info-modal", "style": "display:none;", "onclick": "valCloseMetricInfo()"},
                ui.div(
                    {"class": "val-info-modal-box", "onclick": "event.stopPropagation()"},
                    ui.tags.button("✕", class_="val-info-close", onclick="valCloseMetricInfo()"),
                    ui.tags.h4("", id="val-info-title"),
                    ui.tags.p("", id="val-info-desc"),
                ),
            ),
        ),

        class_="page-container",
    )


# ── Server ────────────────────────────────────────────────────────────────────

@module.server
def valuation_server(input, output, session, active_tab: reactive.value = None):

    _initialized = False  # 일반 변수: effect 자기-재트리거 방지
    open_symbol = reactive.value(None)  # None: 테이블 뷰, str: 해당 종목 상세뷰

    # ── DB 캐시: fmp_metrics 최신 스냅샷. 이 화면 진입 세션 동안 1회만 조회.
    # (일별 배치 갱신 데이터이므로 실시간 신호에 묶지 않음 — 문서 "확정된 설계 원칙" 참고)
    @reactive.calc
    def _db_metrics() -> list[dict]:
        with get_db() as conn:
            return _load_latest_metrics(conn)

    @reactive.calc
    def _scored_records() -> list[dict]:
        records = build_axis_scores(_db_metrics())
        records.sort(key=lambda r: (r["composite_score"] is None, -(r["composite_score"] or 0)))
        return records

    # ── NDX 대비 초과수익률: 개월수 입력 변경 시에만 재조회 (전 종목 배치, 쿼리 2회) ──
    @reactive.calc
    def _ndx_excess_returns() -> dict[str, float | None]:
        months = input.ndx_months() or DEFAULT_NDX_MONTHS
        end_date = date.today()
        start_date = end_date - timedelta(days=30 * months)

        symbols = [r["symbol"] for r in _scored_records()]
        all_symbols = symbols + ["NDX"]

        with get_db() as conn:
            start_prices = _closest_closes_batch(conn, all_symbols, start_date)
            end_prices = _closest_closes_batch(conn, all_symbols, end_date)

        ndx_start = start_prices.get("NDX")
        ndx_end = end_prices.get("NDX")

        return {
            sym: excess_return_vs_benchmark(start_prices.get(sym), end_prices.get(sym), ndx_start, ndx_end)
            for sym in symbols
        }

    @reactive.calc
    def _records_with_ndx() -> list[dict]:
        excess_returns = _ndx_excess_returns()
        records = []
        for r in _scored_records():
            merged = dict(r)
            merged["ndx_excess_return"] = excess_returns.get(r["symbol"])
            records.append(merged)
        return records

    # ── 진단용 참고 지표(pe_divergence) 계산: 이미 로드된 trailing_pe/run_rate_pe로
    # 순수 계산만 하므로 별도 DB/Redis 조회 없음 ──────────────────────────────
    @reactive.calc
    def _records_with_diagnostics() -> list[dict]:
        records = []
        for r in _records_with_ndx():
            merged = dict(r)
            merged["pe_divergence"] = pe_run_rate_divergence(r.get("trailing_pe"), r.get("run_rate_pe"))
            records.append(merged)
        return records

    @reactive.calc
    def _heatmap_scores() -> dict[str, dict]:
        records = _records_with_diagnostics()
        return {
            key: percentile_map(records, key, higher_is_better=hib)
            for key, _, hib in ALL_TABLE_COLUMNS
        }

    # ── 테이블 렌더 (탭 비가시 상태에서는 최초 1회 이후 재전송 안 함) ──────────
    @reactive.effect
    async def _send_table():
        nonlocal _initialized
        if _initialized and active_tab and active_tab.get() != "valuation":
            return
        records = _records_with_diagnostics()
        heatmap = _heatmap_scores()
        await session.send_custom_message("val_table_init", {
            "table_html": _build_table_html(records, heatmap),
            "symbol_clicked_id": session.ns("symbol_clicked"),
            "metric_info": _metric_info_payload(),
        })
        _initialized = True

    # ── 종목(열 헤더) 클릭 → 상세뷰 상태 전환 ─────────────────────────────────
    @reactive.effect
    @reactive.event(input.symbol_clicked)
    def _handle_symbol_click():
        payload = input.symbol_clicked()
        open_symbol.set(payload.get("symbol") if payload else None)

    @reactive.effect
    async def _send_detail():
        symbol = open_symbol.get()
        if not symbol:
            await session.send_custom_message("val_detail_hide", {})
            return
        record = next((r for r in _records_with_diagnostics() if r["symbol"] == symbol), None)
        if not record:
            return
        months = input.ndx_months() or DEFAULT_NDX_MONTHS
        await session.send_custom_message("val_detail_show", {
            "symbol": symbol,
            "detail_html": _build_detail_html(record, months),
        })