"""
FMP 가치평가 스냅샷 엔진

역할:
- 원천 재무/추정치/현재 스냅샷을 읽어 valuation 지표를 계산한다.
- 계산 결과를 fmp_metrics에 upsert한다.
- 빅테크 10개 유니버스 내부 순위를 계산해 로그로 남긴다.

이 파일은 systemd/수동 실행용 얇은 진입점이다.
실제 계산 로직은 app/utils/fmp_valuation.py에 둔다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.db import get_db  # noqa: E402
from app.utils.fmp_valuation import build_axis_scores, build_snapshot_metrics  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


METRIC_COLUMNS = [
    "price",
    "market_cap",
    "enterprise_value",
    "trailing_pe",
    "forward_pe",
    "run_rate_pe",
    "peg",
    "psr",
    "ev_ebitda",
    "ev_fcf",
    "revenue_growth",
    "eps_growth",
    "revenue_cagr_3y",
    "revenue_cagr_5y",
    "eps_cagr_3y",
    "eps_cagr_5y",
    "fcf_cagr_3y",
    "fcf_cagr_5y",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "roe",
    "debt_equity",
    "net_debt_equity",
    "fcf_efficiency",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FMP valuation metrics engine")
    parser.add_argument("--symbol", action="append", help="특정 심볼만 처리 (여러 번 지정 가능)")
    parser.add_argument("--rebuild", action="store_true", help="기존 계산값이 있어도 전부 재계산")
    parser.add_argument("--dry-run", action="store_true", help="DB write 없이 계산만 수행")
    return parser.parse_args()


def _load_symbols(conn, requested_symbols: list[str] | None) -> list[str]:
    if requested_symbols:
        return sorted(set(requested_symbols))

    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM fmp_symbols WHERE is_active = TRUE ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]


def _load_price_snapshots(conn, symbol: str, rebuild: bool) -> list[dict]:
    where_clause = "price IS NOT NULL"
    if not rebuild:
        missing_clause = " OR ".join(f"{column} IS NULL" for column in METRIC_COLUMNS[3:])
        where_clause += f" AND ({missing_clause})"

    sql = f"""
        SELECT symbol, calculated_at, price, market_cap, enterprise_value
        FROM fmp_metrics
        WHERE symbol = %s
          AND {where_clause}
        ORDER BY calculated_at ASC
    """

    with conn.cursor() as cur:
        cur.execute(sql, (symbol,))
        rows = cur.fetchall()

    return [
        {
            "symbol": row[0],
            "calculated_at": row[1],
            "price": row[2],
            "market_cap": row[3],
            "enterprise_value": row[4],
        }
        for row in rows
    ]


def _load_annual_rows(conn, symbol: str) -> list[dict]:
    sql = """
        SELECT fiscal_year, revenue, gross_profit, operating_income, ebitda, net_income,
               eps, total_assets, total_liabilities, total_debt, total_equity, cash,
               operating_cash_flow, capex, free_cash_flow
        FROM fmp_financials
        WHERE symbol = %s
        ORDER BY fiscal_year ASC
    """

    with conn.cursor() as cur:
        cur.execute(sql, (symbol,))
        rows = cur.fetchall()

    return [
        {
            "fiscal_year": row[0],
            "revenue": row[1],
            "gross_profit": row[2],
            "operating_income": row[3],
            "ebitda": row[4],
            "net_income": row[5],
            "eps": row[6],
            "total_assets": row[7],
            "total_liabilities": row[8],
            "total_debt": row[9],
            "total_equity": row[10],
            "cash": row[11],
            "operating_cash_flow": row[12],
            "capex": row[13],
            "free_cash_flow": row[14],
        }
        for row in rows
    ]


def _load_quarterly_rows(conn, symbol: str) -> list[dict]:
    sql = """
        SELECT fiscal_quarter_end, revenue, eps_diluted, net_income, operating_income,
               operating_cash_flow, capex, free_cash_flow
        FROM fmp_quarterly_financials
        WHERE symbol = %s
        ORDER BY fiscal_quarter_end ASC
    """

    with conn.cursor() as cur:
        cur.execute(sql, (symbol,))
        rows = cur.fetchall()

    return [
        {
            "fiscal_quarter_end": row[0],
            "revenue": row[1],
            "eps_diluted": row[2],
            "net_income": row[3],
            "operating_income": row[4],
            "operating_cash_flow": row[5],
            "capex": row[6],
            "free_cash_flow": row[7],
        }
        for row in rows
    ]


def _load_estimate_rows(conn, symbol: str) -> list[dict]:
    sql = """
        SELECT estimate_year, revenue_avg, ebitda_avg, net_income_avg, eps_avg
        FROM fmp_estimates
        WHERE symbol = %s
        ORDER BY estimate_year ASC
    """

    with conn.cursor() as cur:
        cur.execute(sql, (symbol,))
        rows = cur.fetchall()

    return [
        {
            "estimate_year": row[0],
            "revenue_avg": row[1],
            "ebitda_avg": row[2],
            "net_income_avg": row[3],
            "eps_avg": row[4],
        }
        for row in rows
    ]


def _upsert_snapshot(conn, snapshot: dict) -> None:
    sql = """
        INSERT INTO fmp_metrics (
            symbol, calculated_at,
            price, market_cap, enterprise_value,
            trailing_pe, forward_pe, run_rate_pe, peg, psr,
            ev_ebitda, ev_fcf,
            revenue_growth, eps_growth,
            revenue_cagr_3y, revenue_cagr_5y,
            eps_cagr_3y, eps_cagr_5y,
            fcf_cagr_3y, fcf_cagr_5y,
            gross_margin, operating_margin, net_margin,
            fcf_margin, roe, debt_equity, net_debt_equity, fcf_efficiency
        ) VALUES (
            %(symbol)s, %(calculated_at)s,
            %(price)s, %(market_cap)s, %(enterprise_value)s,
            %(trailing_pe)s, %(forward_pe)s, %(run_rate_pe)s, %(peg)s, %(psr)s,
            %(ev_ebitda)s, %(ev_fcf)s,
            %(revenue_growth)s, %(eps_growth)s,
            %(revenue_cagr_3y)s, %(revenue_cagr_5y)s,
            %(eps_cagr_3y)s, %(eps_cagr_5y)s,
            %(fcf_cagr_3y)s, %(fcf_cagr_5y)s,
            %(gross_margin)s, %(operating_margin)s, %(net_margin)s,
            %(fcf_margin)s, %(roe)s, %(debt_equity)s, %(net_debt_equity)s, %(fcf_efficiency)s
        )
        ON CONFLICT (symbol, calculated_at) DO UPDATE SET
            price = EXCLUDED.price,
            market_cap = EXCLUDED.market_cap,
            enterprise_value = EXCLUDED.enterprise_value,
            trailing_pe = EXCLUDED.trailing_pe,
            forward_pe = EXCLUDED.forward_pe,
            run_rate_pe = EXCLUDED.run_rate_pe,
            peg = EXCLUDED.peg,
            psr = EXCLUDED.psr,
            ev_ebitda = EXCLUDED.ev_ebitda,
            ev_fcf = EXCLUDED.ev_fcf,
            revenue_growth = EXCLUDED.revenue_growth,
            eps_growth = EXCLUDED.eps_growth,
            revenue_cagr_3y = EXCLUDED.revenue_cagr_3y,
            revenue_cagr_5y = EXCLUDED.revenue_cagr_5y,
            eps_cagr_3y = EXCLUDED.eps_cagr_3y,
            eps_cagr_5y = EXCLUDED.eps_cagr_5y,
            fcf_cagr_3y = EXCLUDED.fcf_cagr_3y,
            fcf_cagr_5y = EXCLUDED.fcf_cagr_5y,
            gross_margin = EXCLUDED.gross_margin,
            operating_margin = EXCLUDED.operating_margin,
            net_margin = EXCLUDED.net_margin,
            fcf_margin = EXCLUDED.fcf_margin,
            roe = EXCLUDED.roe,
            debt_equity = EXCLUDED.debt_equity,
            net_debt_equity = EXCLUDED.net_debt_equity,
            fcf_efficiency = EXCLUDED.fcf_efficiency
    """

    with conn.cursor() as cur:
        cur.execute(sql, snapshot)


def run(rebuild: bool = False, dry_run: bool = False, requested_symbols: list[str] | None = None):
    grouped_for_scoring: dict[date, list[dict]] = defaultdict(list)

    with get_db() as conn:
        symbols = _load_symbols(conn, requested_symbols)
        if not symbols:
            log.warning("fmp_symbols 테이블에 is_active=TRUE 종목 없음")
            return []

        log.info("평가 대상 %d개: %s", len(symbols), ", ".join(symbols))

        processed = []
        for symbol in symbols:
            annual_rows = _load_annual_rows(conn, symbol)
            quarterly_rows = _load_quarterly_rows(conn, symbol)
            estimate_rows = _load_estimate_rows(conn, symbol)
            price_rows = _load_price_snapshots(conn, symbol, rebuild=rebuild)

            if not price_rows:
                log.info("  [%s] 계산 대상 스냅샷 없음", symbol)
                continue

            log.info(
                "  [%s] annual=%d quarterly=%d estimates=%d snapshots=%d",
                symbol,
                len(annual_rows),
                len(quarterly_rows),
                len(estimate_rows),
                len(price_rows),
            )

            for price_row in price_rows:
                snapshot = build_snapshot_metrics(
                    as_of=price_row["calculated_at"],
                    price_row=price_row,
                    annual_rows=annual_rows,
                    quarterly_rows=quarterly_rows,
                    estimate_rows=estimate_rows,
                )
                processed.append(snapshot)
                grouped_for_scoring[price_row["calculated_at"]].append(snapshot)

                if dry_run:
                    continue
                _upsert_snapshot(conn, snapshot)

        if not dry_run:
            conn.commit()

    for as_of, records in sorted(grouped_for_scoring.items(), key=lambda item: item[0]):
        scored_records = build_axis_scores(records)
        top = sorted(
            [record for record in scored_records if record.get("composite_score") is not None],
            key=lambda record: record["composite_score"],
            reverse=True,
        )[:3]
        if not top:
            continue
        log.info("[%s] 상위 후보: %s", as_of, ", ".join(
            f"{record['symbol']}={record['composite_score']:.2f}"
            for record in top
        ))

    return processed


def main() -> None:
    args = _parse_args()
    run(rebuild=args.rebuild, dry_run=args.dry_run, requested_symbols=args.symbol)


if __name__ == "__main__":
    main()