from fastapi import APIRouter, Depends, HTTPException, Body
from typing import List
from sqlalchemy.orm import Session
from ulid import ULID

from app.dependencies import get_db
from app.models.watchlist import WatchlistResponse, WatchlistCreateRequest
from app.services.watchlist_repository import WatchlistRepository
from app.schemas.watchlist import Watchlist

router = APIRouter()


@router.get("", response_model=List[WatchlistResponse])
def list_watchlists(db: Session = Depends(get_db)):
    """获取所有观察列表"""
    repo = WatchlistRepository(db)
    items = repo.get_active()
    return [WatchlistResponse.model_validate(w) for w in items]


@router.post("", response_model=WatchlistResponse)
async def create_watchlist(
    request: WatchlistCreateRequest = Body(...),
    db: Session = Depends(get_db),
):
    """创建观察列表"""
    repo = WatchlistRepository(db)
    wl = Watchlist(
        id=str(ULID()),
        name=request.name,
        description=request.description,
        list_type=request.list_type,
        status="active",
        config={},
    )
    created = repo.create(wl)
    return WatchlistResponse.model_validate(created)


@router.get("/{watchlist_id}", response_model=WatchlistResponse)
async def get_watchlist(
    watchlist_id: str,
    db: Session = Depends(get_db),
):
    """获取单个观察列表"""
    repo = WatchlistRepository(db)
    item = repo.get(watchlist_id)
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return WatchlistResponse.model_validate(item)


@router.delete("/{watchlist_id}", status_code=204)
async def delete_watchlist(
    watchlist_id: str,
    db: Session = Depends(get_db),
):
    """删除观察列表"""
    repo = WatchlistRepository(db)
    item = repo.get(watchlist_id)
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    repo.delete(item)
