"""Initialize default FundProfile records for fund estimation V1.

Usage:
    python scripts/init_fund_profiles.py
"""
from datetime import date
from app.database import SessionLocal
import app.schemas  # noqa: F401 - ensure ORM registration
from app.schemas.market_snapshot import FundProfile
from ulid import ULID

DEFAULT_FUND_PROFILES = [
    {
        "fund_code": "110011",
        "fund_name": "易方达中小盘混合（示例）",
        "market": "CN",
        "last_nav": 4.1234,
        "last_nav_date": date.today(),
        "beta": 1.0,
        "basket": [
            {"symbol": "CSI300", "weight": 0.45},
            {"symbol": "ZZ500", "weight": 0.35},
            {"symbol": "CYB", "weight": 0.20},
        ],
        "notes": "示例配置，请按真实持仓风格调整权重",
    },
    {
        "fund_code": "001180",
        "fund_name": "广发医药卫生联接（示例）",
        "market": "CN",
        "last_nav": 2.0381,
        "last_nav_date": date.today(),
        "beta": 1.05,
        "basket": [
            {"symbol": "CSI300", "weight": 0.30},
            {"symbol": "CYB", "weight": 0.40},
            {"symbol": "HSTECH", "weight": 0.30},
        ],
        "notes": "示例配置，请按真实风格调整",
    },
    {
        "fund_code": "000638",
        "fund_name": "富国富钱包货币（示例）",
        "market": "CN",
        "last_nav": 1.0000,
        "last_nav_date": date.today(),
        "beta": 0.05,
        "basket": [
            {"symbol": "CSI300", "weight": 0.10},
            {"symbol": "DXY", "weight": 0.90},
        ],
        "notes": "货币类仅示例，建议自定义低波动代理",
    },
]


def upsert_profile(db, item: dict) -> tuple[str, str]:
    row = db.query(FundProfile).filter(FundProfile.fund_code == item["fund_code"]).first()
    if row:
        row.fund_name = item["fund_name"]
        row.market = item["market"]
        row.last_nav = item["last_nav"]
        row.last_nav_date = item["last_nav_date"]
        row.beta = item["beta"]
        row.basket = item["basket"]
        row.notes = item["notes"]
        row.status = "active"
        return item["fund_code"], "updated"

    db.add(FundProfile(
        id=str(ULID()),
        fund_code=item["fund_code"],
        fund_name=item["fund_name"],
        market=item["market"],
        last_nav=item["last_nav"],
        last_nav_date=item["last_nav_date"],
        basket=item["basket"],
        beta=item["beta"],
        status="active",
        notes=item["notes"],
    ))
    return item["fund_code"], "created"


def main():
    db = SessionLocal()
    try:
        stats = {"created": 0, "updated": 0}
        for profile in DEFAULT_FUND_PROFILES:
            _, action = upsert_profile(db, profile)
            stats[action] += 1
        db.commit()
        print(f"FundProfile init done: {stats}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
