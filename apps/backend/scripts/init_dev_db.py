"""初始化开发数据库：建表 + seed mock 数据"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.database import Base, engine
from app.config import settings

print(f"Database: {settings.DATABASE_URL}")

# 导入全部 schema 确保 metadata 完整
import app.schemas  # noqa

print("Creating tables...")
Base.metadata.create_all(bind=engine)
print(f"Tables: {sorted(Base.metadata.tables.keys())}")

from sqlalchemy.orm import Session
with Session(engine) as db:
    from app.services.mock_seeder import seed_mock_data
    stats = seed_mock_data(db)
    print(f"Seeded: {stats}")

print("Done. dev.db is ready.")
