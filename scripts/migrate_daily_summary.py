import json
from pathlib import Path

import openpyxl
import psycopg2

BASE_DIR = Path("/home/ubuntu/asset-cloud")

with open(BASE_DIR / "scheduler" / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

EXCEL_FILE = BASE_DIR / "migration" / "은퇴플랜 ETF_복제.xlsx"

conn = psycopg2.connect(
    host="localhost",
    dbname="assetdb",
    user="jake",
    password=config["db_password"]
)

cur = conn.cursor()

wb = openpyxl.load_workbook(EXCEL_FILE, data_only=True)
ws = wb["데이터누적"]

processed = 0

for row in ws.iter_rows(min_row=3, values_only=True):

    # B열(날짜)이 비어있으면 스킵
    if row[1] is None:
        continue

    date = row[1]
    total_asset = row[2]
    ndx100 = row[3]

    exposure = row[7]
    cash_ratio = row[8]

    x3_ratio = row[9]
    x2_ratio = row[10]
    x1_ratio = row[11]

    cash_flow = row[12] if row[12] is not None else 0

    twr_asset = row[14]

    cash_flow_note = row[15]

    cur.execute(
        """
        INSERT INTO daily_summary (
            date,
            total_asset,
            cash_flow,
            cash_flow_note,
            ndx100,
            exposure,
            cash_ratio,
            x1_ratio,
            x2_ratio,
            x3_ratio,
            twr_asset
        )
        VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
        ON CONFLICT (date)
        DO UPDATE SET
            total_asset    = EXCLUDED.total_asset,
            cash_flow      = EXCLUDED.cash_flow,
            cash_flow_note = EXCLUDED.cash_flow_note,
            ndx100         = EXCLUDED.ndx100,
            exposure       = EXCLUDED.exposure,
            cash_ratio     = EXCLUDED.cash_ratio,
            x1_ratio       = EXCLUDED.x1_ratio,
            x2_ratio       = EXCLUDED.x2_ratio,
            x3_ratio       = EXCLUDED.x3_ratio,
            twr_asset      = EXCLUDED.twr_asset
        """,
        (
            date,
            total_asset,
            cash_flow,
            cash_flow_note,
            ndx100,
            exposure,
            cash_ratio,
            x1_ratio,
            x2_ratio,
            x3_ratio,
            twr_asset,
        ),
    )

    processed += 1

conn.commit()

print(f"완료: {processed}건 처리")

cur.close()
conn.close()