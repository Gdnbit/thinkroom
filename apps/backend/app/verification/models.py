"""Verification Center data models"""
from typing import List, Optional, Any, Dict
from pydantic import BaseModel
from datetime import datetime


class AssertionResult(BaseModel):
    name: str
    assertion_type: str
    status: str
    reason: Optional[str] = None
    expected: Optional[Any] = None
    actual: Optional[Any] = None


class StepResult(BaseModel):
    step_index: int
    action_type: str
    status: str
    duration_ms: int
    input_refs: List[str] = []
    output_refs: List[str] = []
    error: Optional[str] = None
    details: Dict[str, Any] = {}


class VerificationReport(BaseModel):
    run_id: str
    scenario_id: str
    scenario_name: str
    status: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    steps: List[StepResult] = []
    created_objects: Dict[str, List[str]] = {}
    used_objects: Dict[str, List[str]] = {}
    assertions: List[AssertionResult] = []
    retrieval_snapshot: Dict[str, Any] = {}
    summary: Dict[str, int] = {}
    warnings: List[str] = []
    errors: List[str] = []
