"""G2 · /api/v1/fund-profiles — 基金估值配置 CRUD.

让用户在 UI 上管理自己的基金持仓配置，无需直接改数据库。

字段
----
- fund_code (PK)：基金代码（如 110011）
- fund_name：基金名称
- market：市场（CN/HK/US）
- last_nav + last_nav_date：估值锚点（用户从基金公司官网手抄过来，或者由 G3 对账自动同步）
- basket：成分指数权重 [{"symbol": "CSI300", "weight": 0.6}]
- beta：跟踪 beta（默认 1.0）
- status：active / archived（软删）
- notes：备注

端点
----
- GET    /fund-profiles?status=active             列出（可按 status 过滤）
- GET    /fund-profiles/{fund_code}              详情
- POST   /fund-profiles                          创建
- PUT    /fund-profiles/{fund_code}              更新
- DELETE /fund-profiles/{fund_code}              软删（status=archived）

校验
----
- basket 总权重 在 [0.5, 1.5] 范围（允许少量误差，但 0 或 100% 之外不合理）
- 每个 basket 项 symbol 非空 + weight in [0, 1]
- beta in [0, 5]（基金 beta 一般在这个范围）
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from ulid import ULID

from app.database import SessionLocal, safe_commit
from app.schemas.market_snapshot import FundProfile


router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic schemas ────────────────────────────────────────────────


class BasketItem(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    weight: float = Field(..., ge=0, le=1)


class FundProfileIn(BaseModel):
    """创建 / 更新时的输入（fund_code 在 URL 而非 body）."""
    fund_name: str = Field(..., min_length=1, max_length=200)
    market: str = Field("CN", max_length=20)
    last_nav: Optional[float] = Field(None, gt=0, description="基金最近一次官方公布净值")
    last_nav_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD")
    basket: List[BasketItem] = Field(default_factory=list)
    beta: float = Field(1.0, ge=0, le=5)
    status: str = Field("active", pattern="^(active|archived)$")
    notes: Optional[str] = Field(None, max_length=500)

    @field_validator("basket")
    @classmethod
    def _check_basket_weight_sum(cls, v: List[BasketItem]) -> List[BasketItem]:
        if not v:
            return v
        total = sum(item.weight for item in v)
        if total < 0.5 or total > 1.5:
            raise ValueError(
                f"basket 权重总和应在 [0.5, 1.5]（当前 {total:.2f}）；"
                f"基金通常应能 80-120% 跟踪指数组合"
            )
        return v


class FundProfileCreateIn(FundProfileIn):
    fund_code: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_\-]+$")


class FundProfileOut(BaseModel):
    id: str
    fund_code: str
    fund_name: str
    market: str
    last_nav: Optional[float]
    last_nav_date: Optional[str]
    basket: list
    beta: float
    status: str
    notes: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

    class Config:
        from_attributes = True


def _to_out(p: FundProfile) -> FundProfileOut:
    return FundProfileOut(
        id=p.id,
        fund_code=p.fund_code,
        fund_name=p.fund_name,
        market=p.market,
        last_nav=p.last_nav,
        last_nav_date=p.last_nav_date.isoformat() if p.last_nav_date else None,
        basket=p.basket or [],
        beta=p.beta,
        status=p.status,
        notes=p.notes,
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid date: {s} (expected YYYY-MM-DD)")


# ── 端点 ────────────────────────────────────────────────────────────


@router.get("", response_model=List[FundProfileOut])
def list_profiles(
    status: Optional[str] = Query(None, pattern="^(active|archived)$"),
    db: Session = Depends(get_db),
) -> List[FundProfileOut]:
    """列出全部基金（可选按 status 过滤；archived 默认不返回，需显式指定）."""
    q = db.query(FundProfile)
    if status:
        q = q.filter(FundProfile.status == status)
    else:
        # 默认只返回 active
        q = q.filter(FundProfile.status == "active")
    rows = q.order_by(FundProfile.fund_code.asc()).all()
    return [_to_out(p) for p in rows]


@router.get("/{fund_code}", response_model=FundProfileOut)
def get_profile(
    fund_code: str = Path(..., min_length=1, max_length=20),
    db: Session = Depends(get_db),
) -> FundProfileOut:
    p = db.query(FundProfile).filter(FundProfile.fund_code == fund_code).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"fund_code {fund_code} not found")
    return _to_out(p)


@router.post("", response_model=FundProfileOut, status_code=201)
def create_profile(
    body: FundProfileCreateIn,
    db: Session = Depends(get_db),
) -> FundProfileOut:
    existing = db.query(FundProfile).filter(FundProfile.fund_code == body.fund_code).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"fund_code {body.fund_code} already exists; use PUT to update"
        )

    p = FundProfile(
        id=str(ULID()),
        fund_code=body.fund_code,
        fund_name=body.fund_name,
        market=body.market,
        last_nav=body.last_nav,
        last_nav_date=_parse_date(body.last_nav_date),
        basket=[item.model_dump() for item in body.basket],
        beta=body.beta,
        status=body.status,
        notes=body.notes,
    )
    db.add(p)
    safe_commit(db)
    db.refresh(p)
    return _to_out(p)


@router.put("/{fund_code}", response_model=FundProfileOut)
def update_profile(
    body: FundProfileIn,
    fund_code: str = Path(..., min_length=1, max_length=20),
    db: Session = Depends(get_db),
) -> FundProfileOut:
    p = db.query(FundProfile).filter(FundProfile.fund_code == fund_code).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"fund_code {fund_code} not found")

    p.fund_name = body.fund_name
    p.market = body.market
    p.last_nav = body.last_nav
    p.last_nav_date = _parse_date(body.last_nav_date)
    p.basket = [item.model_dump() for item in body.basket]
    p.beta = body.beta
    p.status = body.status
    p.notes = body.notes
    p.updated_at = datetime.utcnow()

    safe_commit(db)
    db.refresh(p)
    return _to_out(p)


@router.delete("/{fund_code}")
def delete_profile(
    fund_code: str = Path(..., min_length=1, max_length=20),
    db: Session = Depends(get_db),
) -> Response:
    """软删：把 status 改为 archived，保留历史 FundEstimateSnapshot 数据完整性."""
    p = db.query(FundProfile).filter(FundProfile.fund_code == fund_code).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"fund_code {fund_code} not found")
    p.status = "archived"
    p.updated_at = datetime.utcnow()
    safe_commit(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
