"""Data Source Management API — 数据源状态 + 限制提醒

端点：
- GET /datasources/dashboard  — 数据源管理面板（状态、健康、提醒）
- GET /datasources/list       — 列出所有数据源（可按类别过滤）
- GET /datasources/reminders  — 获取当前数据源提醒
- GET /datasources/quota      — 获取所有配额状态
"""
from fastapi import APIRouter, Query
from typing import Optional

from app.services.data_source_manager import data_source_manager
from app.services.api_quota import get_all_status

router = APIRouter()


@router.get("/dashboard", response_model=dict)
def datasource_dashboard():
    """数据源管理面板：状态汇总、按类别分组、提醒"""
    return data_source_manager.get_dashboard()


@router.get("/list", response_model=list)
def list_datasources(category: Optional[str] = Query(None, description="过滤类别: market|macro|event|flow|internal")):
    """列出所有数据源及其状态"""
    return data_source_manager.list_sources(category=category)


@router.get("/reminders", response_model=list)
def get_reminders():
    """获取当前需要注意的数据源提醒"""
    return data_source_manager.get_reminders()


@router.get("/quota", response_model=list)
def get_quota_status():
    """获取所有常见资源的 API 配额状态"""
    return get_all_status()
