"""G3 · EOD 对账：fund_estimate_snapshots 加 actual_nav + is_eod_final

Revision ID: 0013_g3_eod_reconciliation
Revises: 0012_drill_pass_fixes
Create Date: 2026-04-25

背景
----
`fund_estimate_snapshots` 一直只记估算 NAV，没有"日终实际收盘 NAV"对账字段。
长期估值可信度无法量化（不知道偏差有多大、是高估还是低估）。

行为
----
- `fund_estimate_snapshots.actual_nav` Float NULL — 用户后填的真实日终 NAV
- `fund_estimate_snapshots.is_eod_final` Boolean DEFAULT 0 NOT NULL —
  是否是当天最后一次估算（EOD 之后由 scheduler 标记，作为对账锚点）
- `ix_fund_est_snap_eod_pending` 复合索引：
  `(fund_code, is_eod_final, actual_nav)` — 加速「列待填的 EOD final」查询

跨方言注意
----------
- BOOLEAN DEFAULT 在 PG 严格类型检查需要写 `'false'` 字符串而不是数字
  （doctor pass 教训），所以这里用 `sa.text("0")` + `existing_server_default`
  保持跨方言一致

降级
----
- 反向 drop 索引 + drop 列；保留所有数据（仅丢失 actual_nav 与 is_eod_final 状态）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers
revision: str = "0013_g3_eod_reconciliation"
down_revision: Union[str, None] = "0012_drill_pass_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    """跨方言列存在判断（幂等保护）."""
    insp = sa.inspect(bind)
    try:
        cols = [c["name"] for c in insp.get_columns(table)]
    except Exception:
        return False
    return column in cols


def _has_index(bind, table: str, index: str) -> bool:
    insp = sa.inspect(bind)
    try:
        idx_names = [i["name"] for i in insp.get_indexes(table)]
    except Exception:
        return False
    return index in idx_names


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # actual_nav: 用户后填的真实 EOD NAV（可空）
    if not _has_column(bind, "fund_estimate_snapshots", "actual_nav"):
        op.add_column(
            "fund_estimate_snapshots",
            sa.Column("actual_nav", sa.Float(), nullable=True),
        )

    # is_eod_final: 当日最后一次估算（scheduler 在 EOD 后置为 True）
    # 跨方言 default：用 sa.text("0") 在 SQLite 是 INT 0，PG 会自动转成 false
    if not _has_column(bind, "fund_estimate_snapshots", "is_eod_final"):
        if dialect == "postgresql":
            # PG 直接用 false 字面量，避免 INT→BOOLEAN 隐式转换报错
            op.add_column(
                "fund_estimate_snapshots",
                sa.Column(
                    "is_eod_final",
                    sa.Boolean(),
                    server_default=sa.text("false"),
                    nullable=False,
                ),
            )
        else:
            op.add_column(
                "fund_estimate_snapshots",
                sa.Column(
                    "is_eod_final",
                    sa.Boolean(),
                    server_default=sa.text("0"),
                    nullable=False,
                ),
            )

    # 复合索引：加速「列待填的 EOD final」查询
    if not _has_index(bind, "fund_estimate_snapshots", "ix_fund_est_snap_eod_pending"):
        op.create_index(
            "ix_fund_est_snap_eod_pending",
            "fund_estimate_snapshots",
            ["fund_code", "is_eod_final", "actual_nav"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_index(bind, "fund_estimate_snapshots", "ix_fund_est_snap_eod_pending"):
        op.drop_index("ix_fund_est_snap_eod_pending", table_name="fund_estimate_snapshots")

    if _has_column(bind, "fund_estimate_snapshots", "is_eod_final"):
        op.drop_column("fund_estimate_snapshots", "is_eod_final")

    if _has_column(bind, "fund_estimate_snapshots", "actual_nav"):
        op.drop_column("fund_estimate_snapshots", "actual_nav")
