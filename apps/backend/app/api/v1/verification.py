"""Verification Center API"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from typing import List, Optional, Any
from sqlalchemy.orm import Session
import asyncio, json as _json
from concurrent.futures import ThreadPoolExecutor
from loguru import logger

from app.config import settings
from app.database import safe_commit
from app.dependencies import get_db
from app.observability.rate_limit import limiter
from app.utils.record_classification import require_test_data_profile
from app.verification.runner import VerificationRunner
from app.verification.models import VerificationReport
from datetime import datetime, timezone

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=2)


async def _run_sync(fn, *args, timeout: float = 60.0):
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, fn, *args),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Verification timed out")


@router.get("/scenarios")
async def list_scenarios():
    """列出所有可用的验证场景（纯文件读取，无需 DB）"""
    runner = VerificationRunner.__new__(VerificationRunner)
    return runner.list_scenarios()


@router.post("/run/{scenario_id}", response_model=VerificationReport)
async def run_scenario(
    scenario_id: str,
    db: Session = Depends(get_db),
):
    """执行指定场景，返回完整 VerificationReport"""
    require_test_data_profile()

    def _run():
        runner = VerificationRunner(db)
        return runner.run_scenario(scenario_id)
    return await _run_sync(_run, timeout=120.0)


def _report_to_item(r: VerificationReport) -> dict:
    return {
        "scenario_id": r.scenario_id,
        "scenario_name": r.scenario_name,
        "status": r.status,
        "duration_ms": r.duration_ms,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "summary": r.summary,
        "errors": r.errors,
        "warnings": r.warnings,
        "assertions": [
            {
                "name": a.name,
                "assertion_type": a.assertion_type,
                "status": a.status,
                "reason": a.reason,
                "expected": a.expected,
                "actual": a.actual,
            }
            for a in r.assertions
        ],
        "steps": [
            {
                "step_index": st.step_index,
                "action_type": st.action_type,
                "status": st.status,
                "duration_ms": st.duration_ms,
                "error": st.error,
                "details": st.details,
            }
            for st in r.steps
        ],
    }


def _run_one_scenario(s: dict) -> dict:
    """执行单个场景（独立 DB session，线程安全）"""
    from app.database import SessionLocal
    _db = SessionLocal()
    try:
        r = VerificationRunner(_db).run_scenario(s["scenario_id"])
        return _report_to_item(r)
    except Exception as exc:
        import traceback as _tb
        return {
            "scenario_id": s["scenario_id"],
            "scenario_name": s.get("name", s["scenario_id"]),
            "status": "error",
            "duration_ms": 0,
            "started_at": None,
            "finished_at": None,
            "summary": {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0},
            "errors": [f"{exc}\n{_tb.format_exc()}"],
            "warnings": [],
            "assertions": [],
            "steps": [],
        }
    finally:
        _db.close()


def _cleanup_test_artifacts():
    """删除 src_test_* / clm_test_* / stg_test_* 前缀的历史测试数据，
    保证每次 run-suite 从干净状态开始，避免 UNIQUE 冲突和断言污染。"""
    from app.database import SessionLocal
    from sqlalchemy import text as _text
    _db = SessionLocal()
    try:
        # 顺序很重要：先删子表（有 FK），再删主表
        # 1) BriefingEvidenceLink → BriefingCard (setup 外键)
        _db.execute(_text(
            "DELETE FROM briefing_evidence_links WHERE briefing_id IN "
            "(SELECT id FROM briefing_cards WHERE setup_id IN "
            " (SELECT id FROM setup_cards WHERE strategy_id IN "
            "  (SELECT id FROM strategy_cards WHERE id IN "
            "   (SELECT strategy_id FROM strategy_source_links WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%'))))"
        ))
        _db.execute(_text(
            "DELETE FROM briefing_cards WHERE setup_id IN "
            "(SELECT id FROM setup_cards WHERE strategy_id IN "
            " (SELECT id FROM strategy_cards WHERE id IN "
            "  (SELECT strategy_id FROM strategy_source_links WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%')))"
        ))
        # 2) SetupCards linked to test strategies
        _db.execute(_text(
            "DELETE FROM setup_cards WHERE strategy_id IN "
            "(SELECT id FROM strategy_cards WHERE id IN "
            " (SELECT strategy_id FROM strategy_source_links WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%'))"
        ))
        # 3) StrategySourceLinks & StrategyCards
        _db.execute(_text(
            "DELETE FROM strategy_source_links WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%'"
        ))
        _db.execute(_text(
            "DELETE FROM strategy_cards WHERE id NOT IN "
            "(SELECT strategy_id FROM strategy_source_links) "
            "AND distilled_by != 'seed' AND distilled_by != 'manual'"
        ))
        # 4) LifecycleLogs for test claims
        _db.execute(_text(
            "DELETE FROM lifecycle_logs WHERE claim_id IN "
            "(SELECT claim_id FROM claim_raw_source_links WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%')"
        ))
        # 5) ClaimRawSourceLinks & ClaimCards
        _test_claim_ids = [r[0] for r in _db.execute(_text(
            "SELECT claim_id FROM claim_raw_source_links WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%'"
        )).fetchall()]
        _db.execute(_text(
            "DELETE FROM claim_raw_source_links WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%'"
        ))
        if _test_claim_ids:
            _ph = ",".join(f"'{c}'" for c in _test_claim_ids)
            _db.execute(_text(f"DELETE FROM claim_cards WHERE id IN ({_ph})"))
        # 6) Seeded test claims / strategies (direct IDs)
        _db.execute(_text("DELETE FROM claim_cards WHERE id LIKE 'clm_test_%'"))
        _db.execute(_text("DELETE FROM strategy_cards WHERE id LIKE 'stg_test_%'"))
        # 7) JobRuns & ProcessingTraces
        _db.execute(_text("DELETE FROM job_runs WHERE related_object_id LIKE 'src_test_%' OR related_object_id LIKE 'src_summary_%'"))
        _db.execute(_text("DELETE FROM processing_traces WHERE source_id LIKE 'src_test_%' OR source_id LIKE 'src_summary_%'"))
        # 8) RawSources themselves (will be re-seeded)
        _db.execute(_text("DELETE FROM raw_sources WHERE id LIKE 'src_test_%' OR id LIKE 'src_summary_%'"))
        safe_commit(_db)
        logger.debug("[Verification] Cleaned up old test artifacts")
    except Exception as e:
        logger.warning(f"[Verification] Cleanup failed (non-fatal): {e}")
        try:
            _db.rollback()
        except Exception:
            pass
    finally:
        _db.close()


def _run_scenarios_sync(scenarios: list[dict], scenario_ids: list[str] | None = None) -> tuple[list[dict], dict]:
    """执行场景。SQLite 模式下顺序执行（防止 database-is-locked / UNIQUE race），
    PostgreSQL 模式下并发执行（max_workers=3）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.config import settings as _settings

    # 清除跨场景 MCP 缓存（每次 run-suite 重新拉取）
    VerificationRunner._mcp_cache.clear()

    # ── 清理历史测试数据，确保幂等 ──
    _cleanup_test_artifacts()

    selected = scenarios
    if scenario_ids:
        wanted = set(scenario_ids)
        selected = [s for s in scenarios if s["scenario_id"] in wanted]

    started_at = datetime.now(timezone.utc)

    _is_sqlite = _settings.DATABASE_URL.startswith("sqlite")
    # SQLite 只允许一个 writer，并发场景会在 LLM 长调用期间互相阻塞
    _workers = 1 if _is_sqlite else 3

    results_map: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_workers) as pool:
        future_to_sid = {pool.submit(_run_one_scenario, s): s["scenario_id"] for s in selected}
        for future in as_completed(future_to_sid):
            sid = future_to_sid[future]
            try:
                results_map[sid] = future.result()
            except Exception as exc:
                results_map[sid] = {
                    "scenario_id": sid, "scenario_name": sid, "status": "error",
                    "duration_ms": 0, "started_at": None, "finished_at": None,
                    "summary": {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0},
                    "errors": [str(exc)], "warnings": [], "assertions": [], "steps": [],
                }

    # 按原始顺序返回
    results = [results_map[s["scenario_id"]] for s in selected if s["scenario_id"] in results_map]

    finished_at = datetime.now(timezone.utc)
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = total - passed
    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round((passed / total) * 100, 2) if total else 0.0,
    }
    return results, summary


@router.post("/run-all")
@limiter.limit(settings.RATE_LIMIT_VERIFICATION)
async def run_all_scenarios(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """依次执行所有场景，SSE 流式推送每个场景结果（限流：RATE_LIMIT_VERIFICATION）"""
    require_test_data_profile()

    from fastapi.responses import StreamingResponse
    import json as _json
    import threading
    import queue as _queue

    runner_for_list = VerificationRunner(db)
    scenarios = [
        s for s in runner_for_list.list_scenarios()
        if s.get("scenario_id") != "scenario_010_intent_recognition"
    ]

    q: _queue.Queue = _queue.Queue()
    DONE = object()

    def _worker():
        """逐个执行场景，直接在本线程运行，避免嵌套线程池导致 SQLite 死锁"""
        from app.database import SessionLocal
        import traceback as _tb
        import time as _time

        results = []
        total = len(scenarios)
        for idx, s in enumerate(scenarios, start=1):
            q.put({
                "type": "progress",
                "phase": "start_scenario",
                "index": idx,
                "total": total,
                "scenario_id": s["scenario_id"],
                "scenario_name": s.get("name", s["scenario_id"]),
                "message": f"开始执行场景 {idx}/{total}: {s.get('name', s['scenario_id'])}",
            })
            _t0 = _time.time()
            try:
                _db = SessionLocal()
                try:
                    r = VerificationRunner(_db).run_scenario(s["scenario_id"])
                    item = _report_to_item(r)
                except Exception as exc:
                    item = {
                        "scenario_id": s["scenario_id"],
                        "scenario_name": s.get("name", s["scenario_id"]),
                        "status": "error",
                        "duration_ms": int((_time.time() - _t0) * 1000),
                        "summary": {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0},
                        "errors": [f"{exc}\n{_tb.format_exc()}"],
                        "warnings": [], "assertions": [], "steps": [],
                    }
                finally:
                    _db.close()
            except Exception as exc:
                item = {
                    "scenario_id": s["scenario_id"],
                    "scenario_name": s.get("name", s["scenario_id"]),
                    "status": "error",
                    "duration_ms": int((_time.time() - _t0) * 1000),
                    "summary": {"passed": 0, "failed": 0, "skipped": 0, "warnings": 0},
                    "errors": [f"DB session error: {exc}"],
                    "warnings": [], "assertions": [], "steps": [],
                }
            results.append(item)
            q.put({"type": "scenario", **item})

        total = len(results)
        passed = sum(1 for r in results if r["status"] == "passed")
        q.put({"type": "summary", "total": total, "passed": passed,
               "failed": total - passed, "results": results})
        q.put(DONE)

    threading.Thread(target=_worker, daemon=True).start()

    async def _stream():
        heartbeat_counter = 0
        while True:
            await asyncio.sleep(0.3)
            items = []
            try:
                while True:
                    item = q.get_nowait()
                    if item is DONE:
                        for i in items:
                            yield f"data: {_json.dumps(i, ensure_ascii=False)}\n\n"
                        return
                    items.append(item)
            except _queue.Empty:
                pass
            if items:
                heartbeat_counter = 0
                for i in items:
                    yield f"data: {_json.dumps(i, ensure_ascii=False)}\n\n"
            else:
                heartbeat_counter += 1
                # 每 5 次空轮询（~1.5s）发一次心跳
                if heartbeat_counter % 5 == 0:
                    yield f"data: {_json.dumps({'type':'heartbeat','ts': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/run-suite")
async def run_suite(
    scenario_ids: List[str] | None = None,
    db: Session = Depends(get_db),
):
    """聚合执行多个场景，返回结构化 JSON 结果（便于界面一键复制）"""
    require_test_data_profile()
    runner_for_list = VerificationRunner(db)
    # 排除：010 意图识别（独立测试）、009 API 健康（前端 Phase 1 已覆盖）
    _exclude = {"scenario_010_intent_recognition", "scenario_009_api_health"}
    scenarios = [
        s for s in runner_for_list.list_scenarios()
        if s.get("scenario_id") not in _exclude
    ]
    results, summary = await _run_sync(_run_scenarios_sync, scenarios, scenario_ids, timeout=900.0)
    return {
        "type": "suite",
        "summary": summary,
        "results": results,
        "copy_text": _build_suite_copy_text(summary, results),
    }


class AiEvaluateRequest(BaseModel):
    """AI 评估请求体"""
    scenario_results: List[Any]  # 场景测试结果数组
    api_results: Optional[List[Any]] = None  # 接口测试结果数组
    total_passed: int = 0
    total_count: int = 0
    api_passed: int = 0
    api_count: int = 0


_AI_EVAL_SYSTEM = """你是 ThinkRoom（金融研究决策辅助系统）的**质量评审 AI**。
系统包含以下核心模块：
- Ingestion Pipeline：原始观点入库 → 文本提取 → 摘要 → Claim 切片 → 质量评分 → 去重 → Embedding
- Strategy Distillation：从长文提炼策略卡片 → SetupCard 评估
- Research Briefing：基于证据生成结构化研报（含支撑点、反面论据、风险提示）
- Committee Debate：四席分析师（基准/概率/风控/市场结构）独立辩论 → 最终裁决
- Lifecycle Management：Claim 过期、反证压制、状态轮转
- Embedding & Semantic Search：向量化存储 + 语义检索
- API Layer：38 个 REST 接口覆盖全部功能

你将收到一次完整的一键测试结果（场景测试 + 接口测试）。请你：

1. **逐场景评审**：对每个场景的断言和步骤做合理性评估。即使全部通过，也要检查：
   - 断言的 expected vs actual 是否真的合理（例如：对象 id 是否符合命名规范）
   - 步骤耗时是否在正常范围（如 LLM 调用应在 5-60s，DB 操作应 <1s）
   - 是否有"虽通过但值得关注"的情况（如边界值、空列表通过等）

2. **接口健康评估**：对 API 响应时间做整体评估，标出响应偏慢（>2s）或异常模式

3. **综合质量评分**：给出 0-100 分的系统健康度评分，并列出：
   - 🟢 **亮点**（表现突出的地方）
   - 🟡 **关注项**（虽通过但需注意）
   - 🔴 **风险项**（如有失败或潜在问题）

4. **改进建议**：基于当前测试覆盖度，给出 2-3 条最值得优先改进的建议

输出格式：中文，Markdown 格式，结构清晰。"""


@router.post("/ai-evaluate")
async def ai_evaluate(req: AiEvaluateRequest):
    """将测试结果发送给 AI 进行质量评审"""
    from app.utils.llm_client import llm_client
    from app.config import settings

    # 构建详细的用户 prompt
    lines = []
    lines.append(f"# 一键测试结果报告")
    lines.append(f"测试时间: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"场景测试: {req.total_passed}/{req.total_count} 通过")
    if req.api_count:
        lines.append(f"接口测试: {req.api_passed}/{req.api_count} 通过")
    lines.append("")

    # 场景明细
    lines.append("## 场景测试明细")
    for res in req.scenario_results:
        sid = res.get("scenario_id", "?")
        sname = res.get("scenario_name", sid)
        status = res.get("status", "unknown")
        dur = res.get("duration_ms", 0)
        sm = res.get("summary", {})
        lines.append(f"\n### [{status.upper()}] {sname} ({sid}) — {dur}ms")
        lines.append(f"断言: ✓{sm.get('passed',0)} ✗{sm.get('failed',0)} -{sm.get('skipped',0)}")

        for a in (res.get("assertions") or []):
            icon = "✓" if a.get("status") == "passed" else "✗"
            lines.append(f"  {icon} {a.get('name')}")
            if a.get("expected") is not None:
                lines.append(f"    预期: {_json.dumps(a['expected'], ensure_ascii=False, default=str)}")
            if a.get("actual") is not None:
                lines.append(f"    实际: {_json.dumps(a['actual'], ensure_ascii=False, default=str)}")
            if a.get("reason"):
                lines.append(f"    原因: {a['reason']}")

        for s in (res.get("steps") or []):
            icon = "✓" if s.get("status") == "completed" else "✗"
            lines.append(f"  {icon} [{s.get('step_index',0)+1}] {s.get('action_type')} ({s.get('duration_ms',0)}ms)")
            if s.get("error"):
                lines.append(f"    错误: {str(s['error']).splitlines()[0]}")
            if s.get("details"):
                d = s["details"]
                preview = ", ".join(f"{k}={_json.dumps(v, ensure_ascii=False, default=str)[:80]}" for k, v in d.items() if v is not None)
                if preview:
                    lines.append(f"    详情: {preview[:300]}")

        for e in (res.get("errors") or []):
            lines.append(f"  ERROR: {str(e).splitlines()[0]}")

    # 接口测试明细
    if req.api_results:
        lines.append("\n## 接口测试明细")
        for api in req.api_results:
            status = api.get("status", "unknown")
            icon = "✓" if status == "success" else "✗"
            lines.append(f"  {icon} {api.get('method','GET')} {api.get('path','')} — {api.get('ms',0)}ms"
                         + (f" ERROR: {api.get('error','')}" if api.get("error") else ""))

    user_prompt = "\n".join(lines)

    # 使用 verification supervisor 模型（gpt-5.4-mini，性价比最优）
    # 评估报告需要较长输出，使用 2500 tokens
    _eval_max_tokens = 2500

    def _call_llm():
        resp, model_used = llm_client.complete_with_role(
            skill_role="extraction",
            system_prompt=_AI_EVAL_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=_eval_max_tokens,
            temperature=0.3,
            model=settings.VERIFICATION_SUPERVISOR_MODEL,
        )
        return {"evaluation": resp, "model": model_used}

    try:
        result = await asyncio.wait_for(asyncio.to_thread(_call_llm), timeout=120)
        return result
    except (asyncio.TimeoutError, Exception) as exc:
        # 主模型失败或超时，尝试 fallback
        logger.warning(f"[ai-evaluate] Primary model failed: {exc}, trying fallback")
        try:
            def _call_fallback():
                resp, model_used = llm_client.complete_with_role(
                    skill_role="extraction",
                    system_prompt=_AI_EVAL_SYSTEM,
                    user_prompt=user_prompt,
                    max_tokens=_eval_max_tokens,
                    temperature=0.3,
                    model=settings.VERIFICATION_SUPERVISOR_FALLBACK,
                )
                return {"evaluation": resp, "model": model_used}
            result = await asyncio.wait_for(asyncio.to_thread(_call_fallback), timeout=120)
            return result
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"AI evaluation timed out (primary + fallback both >120s)")
        except Exception as fallback_exc:
            raise HTTPException(status_code=502, detail=f"AI evaluation failed: {exc}; fallback: {fallback_exc}")


@router.post("/model-ping")
async def model_ping():
    """轻量级模型连通性测试：对每个关键 LLM 角色发送最小 prompt，验证模型可响应。

    优化：使用 ping_model() 专用方法 — 15s 超时、不重试、不 fallback。
    总体超时 120s，单角色超时 20s。10 角色并发 4 路预计 30-60s。
    """
    import time as _time
    from app.utils.llm_client import llm_client
    from app.config import settings

    # 动态发现所有模型角色（新增席位/技能后自动纳入测试）
    from app.utils.role_registry import get_all_roles
    ROLES = [
        {"role": entry.role_key, "label": entry.label, "model": getattr(settings, entry.primary_field, "")}
        for entry in get_all_roles()
        if entry.category != "embed" and getattr(settings, entry.primary_field, "")
    ]

    results = []
    total_t0 = _time.time()
    PER_ROLE_TIMEOUT = 20  # 秒 — 包含 15s HTTP 超时 + 余量

    async def _guarded_ping(role_info: dict) -> dict:
        async with SEM:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(llm_client.ping_model, role_info["role"], 15),
                    timeout=PER_ROLE_TIMEOUT,
                )
                # 补充 label 和 configured_model（ping_model 不知道 label）
                result["label"] = role_info["label"]
                result["configured_model"] = role_info["model"]
                result["actual_model"] = result.get("model", role_info["model"])
                return result
            except asyncio.TimeoutError:
                return {
                    "role": role_info["role"],
                    "label": role_info["label"],
                    "configured_model": role_info["model"],
                    "actual_model": None,
                    "success": False,
                    "response": "",
                    "latency_ms": PER_ROLE_TIMEOUT * 1000,
                    "error": f"[超时] 单角色 {PER_ROLE_TIMEOUT}s 超时",
                }

    # 并发 4 路（比之前的 3 路稍多，因为单次更快了）
    SEM = asyncio.Semaphore(4)

    try:
        results = list(await asyncio.wait_for(
            asyncio.gather(*[_guarded_ping(r) for r in ROLES]),
            timeout=120,
        ))
    except asyncio.TimeoutError:
        # 总体超时 — 返回已收集的部分结果
        logger.warning("[Verification] model-ping overall timeout (120s)")
        if not results:
            results = [{
                "role": "timeout", "label": "总体超时",
                "configured_model": "", "actual_model": None,
                "success": False, "response": "",
                "latency_ms": 120000,
                "error": "模型连通性测试总体超时 (120s)，可能是网络/代理问题",
            }]

    total_ms = int((_time.time() - total_t0) * 1000)
    passed = sum(1 for r in results if r["success"])

    return {
        "results": results,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "duration_ms": total_ms,
    }


@router.post("/ingestion-quality-test")
async def ingestion_quality_test(db: Session = Depends(get_db)):
    """素材解析质量测试：运行 ingestion pipeline 并用 AI 评估非确定性输出"""
    require_test_data_profile()

    from app.verification.ingestion_quality_test import (
        run_ingestion_quality_test, build_ai_eval_prompt,
        build_copyable_report, _INGESTION_EVAL_SYSTEM,
    )
    from app.utils.llm_client import llm_client
    from app.config import settings

    # Step 1: 运行 ingestion pipeline 测试（同步，需要 LLM 调用）
    def _run_test():
        return run_ingestion_quality_test(db)

    try:
        test_data = await asyncio.wait_for(
            asyncio.to_thread(_run_test), timeout=120
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Ingestion quality test timed out (120s)")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Test execution failed: {exc}")

    # Step 2: 构建 AI 评估 prompt 并调用 LLM
    user_prompt = build_ai_eval_prompt(test_data)
    _eval_max_tokens = 3000

    ai_evaluation = None
    ai_model = None

    def _call_ai():
        resp, model_used = llm_client.complete_with_role(
            skill_role="extraction",
            system_prompt=_INGESTION_EVAL_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=_eval_max_tokens,
            temperature=0.3,
            model=settings.VERIFICATION_SUPERVISOR_MODEL,
        )
        return resp, model_used

    def _call_ai_fallback():
        resp, model_used = llm_client.complete_with_role(
            skill_role="extraction",
            system_prompt=_INGESTION_EVAL_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=_eval_max_tokens,
            temperature=0.3,
            model=settings.VERIFICATION_SUPERVISOR_FALLBACK,
        )
        return resp, model_used

    try:
        ai_evaluation, ai_model = await asyncio.wait_for(
            asyncio.to_thread(_call_ai), timeout=45
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning(f"[ingestion-quality-test] AI eval primary failed: {exc}, trying fallback")
        try:
            ai_evaluation, ai_model = await asyncio.wait_for(
                asyncio.to_thread(_call_ai_fallback), timeout=45
            )
        except asyncio.TimeoutError:
            ai_evaluation = "AI 评估超时（主模型+备用模型均超过 45s）"
            ai_model = "timeout"
        except Exception as e2:
            ai_evaluation = f"AI 评估调用失败: {e2}"
            ai_model = "failed"

    # Step 3: 构建可复制报告
    copyable_report = build_copyable_report(test_data, ai_evaluation)

    return {
        "test_data": test_data,
        "ai_evaluation": ai_evaluation,
        "ai_model": ai_model,
        "copyable_report": copyable_report,
    }


def _build_suite_copy_text(summary: dict, results: list[dict]) -> str:
    lines: list[str] = []
    lines.append("=== 验证场景套件报告 ===")
    lines.append(f"开始: {summary.get('started_at')}")
    lines.append(f"结束: {summary.get('finished_at')}")
    lines.append(f"耗时: {summary.get('duration_ms')}ms")
    lines.append(
        f"汇总: 共{summary.get('total')}个场景  ✓{summary.get('passed')} ✗{summary.get('failed')}  通过率={summary.get('pass_rate')}%"
    )
    lines.append("")
    lines.append("--- 场景明细（完整调试信息） ---")
    for r in results:
        lines.append(
            f"[{str(r.get('status', 'unknown')).upper()}] {r.get('scenario_name')} ({r.get('scenario_id')}) [{r.get('duration_ms')}ms]"
        )
        sm = r.get("summary") or {}
        lines.append(
            f"  断言: ✓{sm.get('passed', 0)} ✗{sm.get('failed', 0)} -{sm.get('skipped', 0)}  warnings={sm.get('warnings', 0)}"
        )

        # 所有步骤详情
        for s in (r.get("steps") or []):
            s_icon = "✓" if s.get("status") == "completed" else "✗" if s.get("status") == "failed" else "-"
            s_out = ""
            if s.get("output_refs"):
                s_out = f" → [{', '.join(s['output_refs'])}]"
            lines.append(f"  {s_icon} [{s.get('step_index', 0)+1}] {s.get('action_type')} ({s.get('duration_ms', 0)}ms){s_out}")
            if s.get("error"):
                lines.append(f"    error: {str(s['error']).splitlines()[0]}")
            if s.get("details"):
                d = s["details"]
                preview = ", ".join(
                    f"{k}={_json.dumps(v, ensure_ascii=False, default=str)[:80]}"
                    for k, v in d.items() if v is not None
                )
                if preview:
                    lines.append(f"    details: {preview[:300]}")

        # 所有断言详情（包括通过的）
        for a in (r.get("assertions") or []):
            a_icon = "✓" if a.get("status") == "passed" else "✗" if a.get("status") == "failed" else "-"
            a_type = a.get("assertion_type") or a.get("name") or "?"
            a_name = a.get("name", "")
            a_name_str = f" {a_name}" if a_name and a_name != a_type else ""
            reason_str = f" :: {a['reason']}" if a.get("reason") else ""
            lines.append(f"  {a_icon} [{a_type}]{a_name_str}{reason_str}")
            if a.get("expected") is not None or a.get("actual") is not None:
                lines.append(f"    expected={_json.dumps(a.get('expected'), ensure_ascii=False, default=str)}  actual={_json.dumps(a.get('actual'), ensure_ascii=False, default=str)}")

        for e in (r.get("errors") or []):
            lines.append(f"  ERROR: {str(e).splitlines()[0]}")
        lines.append("")

    return "\n".join(lines)
