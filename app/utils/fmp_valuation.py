from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import isclose
from typing import Iterable


def to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_div(numerator, denominator) -> float | None:
    num = to_float(numerator)
    den = to_float(denominator)
    if num is None or den is None or den == 0:
        return None
    return num / den


def cagr(start_value, end_value, years: int) -> float | None:
    start = to_float(start_value)
    end = to_float(end_value)
    if start is None or end is None or years <= 0:
        return None
    if start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def _sorted_desc_by_date(rows: list[dict], key: str) -> list[dict]:
    return sorted(rows, key=lambda row: row.get(key) or date.min)


def latest_row_on_or_before(rows: list[dict], key: str, as_of: date) -> dict | None:
    candidate = None
    for row in rows:
        row_date = row.get(key)
        if row_date is None or row_date > as_of:
            continue
        candidate = row
    return candidate


def row_by_year(rows: list[dict], year_key: str, target_year: int) -> dict | None:
    for row in rows:
        if row.get(year_key) == target_year:
            return row
    return None


def latest_row_on_or_before_year(rows: list[dict], year_key: str, as_of_year: int) -> dict | None:
    candidate = None
    for row in rows:
        row_year = row.get(year_key)
        if row_year is None or row_year > as_of_year:
            continue
        if candidate is None or row_year > candidate[year_key]:
            candidate = row
    return candidate


def sum_latest_n(rows: list[dict], value_key: str, date_key: str, as_of: date, count: int = 4) -> float | None:
    eligible = [row for row in rows if row.get(date_key) is not None and row[date_key] <= as_of]
    if len(eligible) < count:
        return None
    selected = sorted(eligible, key=lambda row: row[date_key])[-count:]
    total = 0.0
    for row in selected:
        value = to_float(row.get(value_key))
        if value is None:
            return None
        total += value
    return total


def sum_latest_n_fcf(rows: list[dict], date_key: str, as_of: date, count: int = 4) -> float | None:
    eligible = [row for row in rows if row.get(date_key) is not None and row[date_key] <= as_of]
    if len(eligible) < count:
        return None
    selected = sorted(eligible, key=lambda row: row[date_key])[-count:]
    total = 0.0
    for row in selected:
        fcf = to_float(row.get("free_cash_flow"))
        if fcf is None:
            ocf = to_float(row.get("operating_cash_flow"))
            capex = to_float(row.get("capex"))
            if ocf is None or capex is None:
                return None
            fcf = ocf + capex
        total += fcf
    return total


def ttm_yoy_growth(rows: list[dict], value_key: str, date_key: str, as_of: date) -> float | None:
    eligible = [row for row in rows if row.get(date_key) is not None and row[date_key] <= as_of]
    if len(eligible) < 8:
        return None
    eligible = sorted(eligible, key=lambda row: row[date_key])
    current = 0.0
    prior = 0.0
    for row in eligible[-4:]:
        value = to_float(row.get(value_key))
        if value is None:
            return None
        current += value
    for row in eligible[-8:-4]:
        value = to_float(row.get(value_key))
        if value is None:
            return None
        prior += value
    if prior == 0:
        return None
    return (current / prior) - 1.0


def percentile_scores(values: list[float | None], higher_is_better: bool = True) -> list[float | None]:
    valid = [(idx, to_float(value)) for idx, value in enumerate(values) if to_float(value) is not None]
    if not valid:
        return [None for _ in values]

    ordered = sorted(valid, key=lambda item: item[1])
    n = len(ordered)
    if n == 1:
        scores = [None for _ in values]
        scores[ordered[0][0]] = 100.0
        return scores

    scores = [None for _ in values]
    pos = 1
    i = 0
    while i < n:
        j = i + 1
        current_value = ordered[i][1]
        while j < n and isclose(ordered[j][1], current_value, rel_tol=1e-12, abs_tol=1e-12):
            j += 1
        avg_rank = (pos + (pos + (j - i) - 1)) / 2.0
        raw_score = 100.0 * (avg_rank - 1.0) / (n - 1.0)
        if higher_is_better:
            score = raw_score
        else:
            score = 100.0 - raw_score
        for k in range(i, j):
            scores[ordered[k][0]] = round(score, 2)
        pos += (j - i)
        i = j
    return scores


def percentile_map(records: list[dict], metric_key: str, higher_is_better: bool = True) -> dict[str, float | None]:
    values = [record.get(metric_key) for record in records]
    scores = percentile_scores(values, higher_is_better=higher_is_better)
    return {
        record["symbol"]: score
        for record, score in zip(records, scores)
    }


def score_bucket(records: list[dict], axis_weights: list[tuple[str, float, bool]]) -> dict[str, float | None]:
    if not records:
        return {}

    metric_scores = {
        metric_key: percentile_map(records, metric_key, higher_is_better=not lower_is_better)
        for metric_key, _, lower_is_better in axis_weights
    }

    output: dict[str, float | None] = {}
    for record in records:
        symbol = record["symbol"]
        total_weight = 0.0
        weighted_score = 0.0
        for metric_key, weight, lower_is_better in axis_weights:
            score = metric_scores[metric_key].get(symbol)
            if score is None:
                continue
            total_weight += weight
            weighted_score += score * weight
        output[symbol] = round(weighted_score / total_weight, 2) if total_weight else None
    return output


VALUTATION_AXIS_WEIGHTS = [
    ("forward_pe", 35.0, True),
    ("ev_fcf", 30.0, True),
    ("trailing_pe", 20.0, True),
    ("psr", 15.0, True),
]

GROWTH_AXIS_WEIGHTS = [
    ("eps_cagr_3y", 40.0, False),
    ("revenue_cagr_3y", 35.0, False),
    ("fcf_cagr_3y", 25.0, False),
]

QUALITY_AXIS_WEIGHTS = [
    ("operating_margin", 30.0, False),
    ("fcf_margin", 30.0, False),
    ("gross_margin", 20.0, False),
    ("roe", 20.0, False),
]

RISK_AXIS_WEIGHTS = [
    ("net_debt_equity", 60.0, True),
    ("debt_equity", 40.0, True),
]

OVERALL_AXIS_WEIGHTS = {
    "valuation": 25.0,
    "growth": 35.0,
    "quality": 30.0,
    "risk": 10.0,
}


def build_snapshot_metrics(as_of: date, price_row: dict, annual_rows: list[dict], quarterly_rows: list[dict], estimate_rows: list[dict]) -> dict:
    latest_annual = latest_row_on_or_before_year(annual_rows, "fiscal_year", as_of.year)
    latest_quarter = latest_row_on_or_before(quarterly_rows, "fiscal_quarter_end", as_of)

    ttm_revenue = sum_latest_n(quarterly_rows, "revenue", "fiscal_quarter_end", as_of, count=4)
    ttm_eps = sum_latest_n(quarterly_rows, "eps_diluted", "fiscal_quarter_end", as_of, count=4)
    ttm_fcf = sum_latest_n_fcf(quarterly_rows, "fiscal_quarter_end", as_of, count=4)

    price = to_float(price_row.get("price"))
    market_cap = to_float(price_row.get("market_cap"))
    enterprise_value = to_float(price_row.get("enterprise_value"))

    forward_eps = None
    if estimate_rows:
        future_year = as_of.year + 1
        estimate = row_by_year(estimate_rows, "estimate_year", future_year)
        if estimate is None:
            estimate = row_by_year(estimate_rows, "estimate_year", as_of.year)
        forward_eps = to_float(estimate.get("eps_avg")) if estimate else None

    annual_ebitda = to_float(latest_annual.get("ebitda")) if latest_annual else None
    annual_revenue = to_float(latest_annual.get("revenue")) if latest_annual else None
    annual_gross_profit = to_float(latest_annual.get("gross_profit")) if latest_annual else None
    annual_operating_income = to_float(latest_annual.get("operating_income")) if latest_annual else None
    annual_net_income = to_float(latest_annual.get("net_income")) if latest_annual else None
    annual_equity = to_float(latest_annual.get("total_equity")) if latest_annual else None
    annual_debt = to_float(latest_annual.get("total_debt")) if latest_annual else None
    annual_cash = to_float(latest_annual.get("cash")) if latest_annual else None
    annual_fcf = to_float(latest_annual.get("free_cash_flow")) if latest_annual else None

    current_q_eps = to_float(latest_quarter.get("eps_diluted")) if latest_quarter else None

    revenue_growth = ttm_yoy_growth(quarterly_rows, "revenue", "fiscal_quarter_end", as_of)
    eps_growth = ttm_yoy_growth(quarterly_rows, "eps_diluted", "fiscal_quarter_end", as_of)

    latest_annual_year = latest_annual.get("fiscal_year") if latest_annual else None

    revenue_3y_row = row_by_year(annual_rows, "fiscal_year", latest_annual_year - 3) if latest_annual_year is not None else None
    revenue_5y_row = row_by_year(annual_rows, "fiscal_year", latest_annual_year - 5) if latest_annual_year is not None else None
    revenue_cagr_3y = cagr(revenue_3y_row.get("revenue") if revenue_3y_row else None, annual_revenue, 3)
    revenue_cagr_5y = cagr(revenue_5y_row.get("revenue") if revenue_5y_row else None, annual_revenue, 5)

    eps_3y_row = row_by_year(annual_rows, "fiscal_year", latest_annual_year - 3) if latest_annual_year is not None else None
    eps_5y_row = row_by_year(annual_rows, "fiscal_year", latest_annual_year - 5) if latest_annual_year is not None else None
    eps_cagr_3y = cagr(eps_3y_row.get("eps") if eps_3y_row else None, to_float(latest_annual.get("eps")) if latest_annual else None, 3)
    eps_cagr_5y = cagr(eps_5y_row.get("eps") if eps_5y_row else None, to_float(latest_annual.get("eps")) if latest_annual else None, 5)

    fcf_3y_row = row_by_year(annual_rows, "fiscal_year", latest_annual_year - 3) if latest_annual_year is not None else None
    fcf_5y_row = row_by_year(annual_rows, "fiscal_year", latest_annual_year - 5) if latest_annual_year is not None else None
    fcf_cagr_3y = cagr(fcf_3y_row.get("free_cash_flow") if fcf_3y_row else None, annual_fcf, 3)
    fcf_cagr_5y = cagr(fcf_5y_row.get("free_cash_flow") if fcf_5y_row else None, annual_fcf, 5)

    trailing_pe = safe_div(price, ttm_eps)
    run_rate_pe = safe_div(price, current_q_eps * 4 if current_q_eps is not None else None)
    forward_pe = safe_div(price, forward_eps)
    psr = safe_div(market_cap, ttm_revenue)
    ev_ebitda = safe_div(enterprise_value, annual_ebitda)
    ev_fcf = safe_div(enterprise_value, ttm_fcf)

    gross_margin = safe_div(annual_gross_profit, annual_revenue)
    operating_margin = safe_div(annual_operating_income, annual_revenue)
    net_margin = safe_div(annual_net_income, annual_revenue)
    fcf_margin = safe_div(ttm_fcf, ttm_revenue)
    roe = safe_div(annual_net_income, annual_equity)
    fcf_efficiency = safe_div(annual_fcf, annual_net_income)

    debt_equity = safe_div(annual_debt, annual_equity)
    net_debt = None
    if annual_debt is not None and annual_cash is not None:
        net_debt = annual_debt - annual_cash
    net_debt_equity = safe_div(net_debt, annual_equity)

    peg = None
    if trailing_pe is not None and eps_cagr_3y is not None and eps_cagr_3y > 0:
        peg = trailing_pe / (eps_cagr_3y * 100.0)

    return {
        "symbol": price_row["symbol"],
        "calculated_at": as_of,
        "price": price,
        "market_cap": market_cap,
        "enterprise_value": enterprise_value,
        "trailing_pe": trailing_pe,
        "forward_pe": forward_pe,
        "run_rate_pe": run_rate_pe,
        "peg": peg,
        "psr": psr,
        "ev_ebitda": ev_ebitda,
        "ev_fcf": ev_fcf,
        "revenue_growth": revenue_growth,
        "eps_growth": eps_growth,
        "revenue_cagr_3y": revenue_cagr_3y,
        "revenue_cagr_5y": revenue_cagr_5y,
        "eps_cagr_3y": eps_cagr_3y,
        "eps_cagr_5y": eps_cagr_5y,
        "fcf_cagr_3y": fcf_cagr_3y,
        "fcf_cagr_5y": fcf_cagr_5y,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "fcf_margin": fcf_margin,
        "roe": roe,
        "debt_equity": debt_equity,
        "net_debt_equity": net_debt_equity,
        "fcf_efficiency": fcf_efficiency,
    }


def build_axis_scores(records: list[dict]) -> list[dict]:
    if not records:
        return []

    valuation_map = score_bucket(records, VALUTATION_AXIS_WEIGHTS)
    growth_map = score_bucket(records, GROWTH_AXIS_WEIGHTS)
    quality_map = score_bucket(records, QUALITY_AXIS_WEIGHTS)
    risk_map = score_bucket(records, RISK_AXIS_WEIGHTS)

    enriched = []
    for record in records:
        valuation_score = valuation_map.get(record["symbol"])
        growth_score = growth_map.get(record["symbol"])
        quality_score = quality_map.get(record["symbol"])
        risk_score = risk_map.get(record["symbol"])

        weighted_sum = 0.0
        weight_total = 0.0
        for axis_name, axis_weight in OVERALL_AXIS_WEIGHTS.items():
            axis_score = {
                "valuation": valuation_score,
                "growth": growth_score,
                "quality": quality_score,
                "risk": risk_score,
            }[axis_name]
            if axis_score is None:
                continue
            weighted_sum += axis_score * axis_weight
            weight_total += axis_weight

        enriched.append({
            **record,
            "valuation_score": valuation_score,
            "growth_score": growth_score,
            "quality_score": quality_score,
            "risk_score": risk_score,
            "composite_score": round(weighted_sum / weight_total, 2) if weight_total else None,
        })

    return enriched


def excess_return_vs_benchmark(
    symbol_start_price,
    symbol_end_price,
    benchmark_start_price,
    benchmark_end_price,
) -> float | None:
    symbol_ratio = safe_div(symbol_end_price, symbol_start_price)
    benchmark_ratio = safe_div(benchmark_end_price, benchmark_start_price)
    if symbol_ratio is None or benchmark_ratio is None:
        return None
    return (symbol_ratio - 1.0) - (benchmark_ratio - 1.0)