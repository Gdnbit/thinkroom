"""CRUD Repository 基类"""
from typing import TypeVar, Generic, Type, Optional, List, Any
from sqlalchemy.orm import Session
from app.database import Base, _write_lock, is_sqlite

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType], db: Session):
        self.model = model
        self.db = db

    def get(self, id: str) -> Optional[ModelType]:
        return self.db.query(self.model).filter(self.model.id == id).first()

    def get_multi(
        self,
        *,
        skip: int = 0,
        limit: int = 20,
        filters: Optional[List[Any]] = None,
    ) -> List[ModelType]:
        q = self.db.query(self.model)
        if filters:
            for f in filters:
                q = q.filter(f)
        return q.offset(skip).limit(limit).all()

    def count(self, filters: Optional[List[Any]] = None) -> int:
        q = self.db.query(self.model)
        if filters:
            for f in filters:
                q = q.filter(f)
        return q.count()

    def _commit(self) -> None:
        """Commit with write lock for SQLite safety."""
        if is_sqlite():
            with _write_lock:
                self.db.commit()
        else:
            self.db.commit()

    def create(self, obj: ModelType) -> ModelType:
        self.db.add(obj)
        self._commit()
        self.db.refresh(obj)
        return obj

    def update(self, obj: ModelType, data: dict) -> ModelType:
        for key, value in data.items():
            setattr(obj, key, value)
        self._commit()
        self.db.refresh(obj)
        return obj

    def delete(self, obj: ModelType) -> None:
        self.db.delete(obj)
        self._commit()

    def bulk_create(self, objs: List[ModelType]) -> List[ModelType]:
        self.db.add_all(objs)
        self._commit()
        for obj in objs:
            self.db.refresh(obj)
        return objs
