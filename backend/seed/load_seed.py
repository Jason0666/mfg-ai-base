"""DEMO 模式业务种子数据初始化（§9.5）。

在 backend/ 工作目录下运行：python -m seed.load_seed
启动时由 main.py lifespan 在 DEMO_MODE 下自动调用 init_seed()。

种子表：
- biz_prod_metric：90 天 × 4 条产线（A/B/C/D）每日生产指标
  人为植入异常：2026-09-11 ~ 2026-09-13 A线更换新物料批次（MB-NEW-0911），
  良率从约 97% 下滑至 88-90%，2026-09-14 换回原批次后恢复，用于演示归因能力。

幂等：表存在且行数=360 时跳过。
"""
from __future__ import annotations

import logging
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import List

from app.config import settings

logger = logging.getLogger(__name__)

# 数据窗口：2026-06-19 ~ 2026-09-16（90 天，今天 2026-09-17）
START_DATE = date(2026, 6, 19)
DAYS = 90

# A线异常窗口（换料批次导致良率下滑）
ANOMALY_LINE = "A线"
ANOMALY_DATES = {date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)}
ANOMALY_BATCH = "MB-NEW-0911"

# 各产线基准参数（基准良率 %，基准日产量，停机基数 min）
LINE_BASE = {
    "A线": {"yield": 97.0, "output": 1200, "down": 22},
    "B线": {"yield": 96.5, "output": 1100, "down": 26},
    "C线": {"yield": 96.0, "output": 1000, "down": 30},
    "D线": {"yield": 97.2, "output": 1150, "down": 20},
}

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS biz_prod_metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    line TEXT NOT NULL,
    output_qty INTEGER NOT NULL,
    good_qty INTEGER NOT NULL,
    defect_qty INTEGER NOT NULL,
    yield_rate REAL NOT NULL,
    downtime_min INTEGER NOT NULL,
    material_batch TEXT NOT NULL,
    UNIQUE(date, line)
)
"""

EXPECTED_ROWS = DAYS * len(LINE_BASE)  # 360


def _deterministic_wave(d: date, line_idx: int) -> float:
    """确定性的日波动（-0.8 ~ +0.8 个百分点），避免每次启动数据变化。"""
    ordinal = d.toordinal() + line_idx * 7
    return 0.8 * math.sin(ordinal / 4.5) + 0.25 * math.sin(ordinal / 11.0 + line_idx)


def _output_wave(d: date, line_idx: int) -> int:
    """产量确定性波动（±3%）。"""
    ordinal = d.toordinal() + line_idx * 3
    return int(round(2.0 * math.sin(ordinal / 7.0) + 1.0 * math.cos(ordinal / 13.0)))


def _material_batch(d: date, line_name: str) -> str:
    """物料批次号：每月 1 日 / 16 日换批。异常窗口内 A 线使用新供应商批次。"""
    if line_name == ANOMALY_LINE and d in ANOMALY_DATES:
        return ANOMALY_BATCH
    # 所属半月批次
    if d.day <= 15:
        period = date(d.year, d.month, 1)
    else:
        period = date(d.year, d.month, 16)
    suffix = line_name[0]
    return f"MB-{period.strftime('%y%m%d')}-{suffix}"


def _build_rows() -> List[tuple]:
    rows: List[tuple] = []
    for day_offset in range(DAYS):
        d = START_DATE + timedelta(days=day_offset)
        for line_idx, (line_name, base) in enumerate(LINE_BASE.items()):
            wave = _deterministic_wave(d, line_idx)
            out_wave = _output_wave(d, line_idx)
            output_qty = base["output"] + int(base["output"] * 0.03 * out_wave / 3.0)
            downtime = base["down"] + (d.toordinal() + line_idx) % 18

            yield_rate = base["yield"] + wave
            # 植入异常：A线换料批次，良率掉到 88-90%，停机时间上升
            if line_name == ANOMALY_LINE and d in ANOMALY_DATES:
                anomaly_yield = {
                    date(2026, 9, 11): 88.6,
                    date(2026, 9, 12): 89.4,
                    date(2026, 9, 13): 90.2,
                }
                yield_rate = anomaly_yield[d] + (wave * 0.2)
                downtime = {date(2026, 9, 11): 85, date(2026, 9, 12): 68, date(2026, 9, 13): 52}[d]

            yield_rate = round(yield_rate, 2)
            good_qty = int(round(output_qty * yield_rate / 100.0))
            defect_qty = output_qty - good_qty
            batch = _material_batch(d, line_name)
            rows.append(
                (d.isoformat(), line_name, output_qty, good_qty, defect_qty,
                 yield_rate, downtime, batch)
            )
    return rows


def init_seed() -> None:
    """DEMO 模式初始化种子数据（幂等）。"""
    if not settings.DEMO_MODE:
        return

    db_path = Path(settings.SQLITE_PATH)
    if not db_path.is_absolute():
        # uvicorn 工作目录为 backend/
        db_path = Path(settings.BASE_DIR).resolve() / settings.SQLITE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(TABLE_DDL)
        count = conn.execute("SELECT COUNT(*) FROM biz_prod_metric").fetchone()[0]
        if count >= EXPECTED_ROWS:
            logger.info("seed biz_prod_metric already loaded: %d rows, skip", count)
            return
        if count > 0:
            # 部分数据：清空重建，保证异常窗口完整
            conn.execute("DELETE FROM biz_prod_metric")
        rows = _build_rows()
        conn.executemany(
            """
            INSERT INTO biz_prod_metric
                (date, line, output_qty, good_qty, defect_qty,
                 yield_rate, downtime_min, material_batch)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        logger.info("seed biz_prod_metric loaded: %d rows (%d days × %d lines)",
                    len(rows), DAYS, len(LINE_BASE))
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    init_seed()
