"""素材解析质量测试模块

对 Ingestion Pipeline 的非确定性输出进行 AI 质量评估：
- 文本拆分（chunking）
- 摘要生成（summary）
- 观点抽取（claim extraction）
- 质量评分 + 去重

返回结构化报告：输入 / 预期 / 实际 / 评估结果 / 评估原因 / 不足之处
"""
import time
import json as _json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from loguru import logger


#  测试样本 
TEST_SAMPLES: List[Dict[str, Any]] = [
    {
        "id": "sample_short",
        "name": "短文本：单条 BTC 观点",
        "text": (
            "知名分析师 PlanB 在最新推文中指出，比特币的 Stock-to-Flow 模型仍然有效，"
            "预计 BTC 将在 2026 年底前突破 15 万美元。他认为当前价格被严重低估，"
            "主要驱动力来自减半后的供给收缩和机构持续买入。"
            "但他也警告，如果美联储意外加息，短期可能回调至 6 万美元支撑位。"
        ),
        "expect": {
            "summary_level": "L0",
            "summary_desc": "短文本（<500字）应直通作为摘要，不调用 LLM",
            "chunks": "1 个（文本短，无需拆分）",
            "claims_min": 1,
            "claims_desc": "至少提取 1 条 BTC 相关观点，stance 应为 bullish 或 conditional",
            "asset_scope_should_contain": ["BTC", "比特币"],
            "quality_grade_min": "C",
        },
    },
    {
        "id": "sample_medium",
        "name": "中等文本：多资产宏观分析",
        "text": (
            "## 2026年Q2宏观展望：流动性拐点与资产配置\n\n"
            "### 一、美联储政策转向\n"
            "美联储在3月会议上维持利率不变，但点阵图显示年内可能降息2次。"
            "市场已经开始定价6月首次降息的预期，CME FedWatch工具显示概率升至72%。"
            "我们认为，如果4月非农数据继续走弱（低于15万），6月降息将成为确定性事件。\n\n"
            "### 二、美元与黄金\n"
            "美元指数（DXY）已从年初的106回落至102附近，技术面呈现头肩顶形态。"
            "若跌破100整数关口，可能引发新一轮美元抛售。"
            "黄金作为降息周期的核心受益资产，我们上调目标价至2800美元/盎司。"
            "白银因工业需求复苏，可能表现优于黄金，金银比有望从当前85降至75。\n\n"
            "### 三、加密货币市场\n"
            "BTC在减半后表现符合历史规律，当前处于蓄力阶段。链上数据显示："
            "长期持有者（LTH）持仓占比升至78%，创历史新高；"
            "交易所余额持续下降，供给端收紧明显。"
            "ETH的Pectra升级预计在Q2完成，可能催化新一轮DeFi热潮。"
            "我们对BTC维持12万-15万的Q2目标区间，ETH目标6000-8000。\n\n"
            "### 四、风险提示\n"
            "1. 地缘政治风险：中东局势升级可能推高油价，引发滞胀担忧\n"
            "2. 日本央行意外加息可能引发套利交易平仓\n"
            "3. 美国大选不确定性可能在Q3加剧市场波动\n\n"
            "### 五、配置建议\n"
            "- 增配：黄金（20%）、BTC（15%）、美债长端（10%）\n"
            "- 标配：美股科技（25%）、ETH生态（5%）\n"
            "- 减配：美元现金、A股周期股\n"
        ),
        "expect": {
            "summary_level": "L1",
            "summary_desc": "中等文本（500-6000字）应调用 LLM 直接生成摘要",
            "chunks": "1 个（<32000字，单块处理）",
            "claims_min": 3,
            "claims_desc": "至少提取 3 条观点，覆盖美联储/黄金/BTC 等多资产",
            "asset_scope_should_contain": ["BTC", "黄金", "GOLD", "DXY", "ETH"],
            "quality_grade_min": "B",
        },
    },
    {
        "id": "sample_opinion",
        "name": "辩论性文本：多空对立观点",
        "text": (
            "## 比特币是否已见顶？多空激辩\n\n"
            "【多方观点  机构分析师 Michael Saylor】\n"
            "比特币作为数字黄金的叙事正在被机构验证。MicroStrategy持仓已超50万枚BTC，"
            "BlackRock的IBIT ETF管理规模突破600亿美元。从链上数据看，"
            "鲸鱼地址（>1000 BTC）在过去30天净增1.2万枚，这是典型的积累信号。"
            "S2F模型指向2026年底20万美元，即使保守估计也应在12万以上。"
            "当前价格是绝佳的建仓机会，建议配置资产组合的10-15%。\n\n"
            "【空方观点  对冲基金经理 Peter Schiff】\n"
            "比特币完全依赖更大傻瓜理论（Greater Fool Theory），没有内在价值。"
            "当前价格已透支了所有利好：减半、ETF、降息预期。"
            "历史数据显示，每次减半后的涨幅在递减：第一次涨100倍，第二次涨30倍，"
            "第三次仅涨7倍。如果这个趋势延续，本轮高点不会超过10万美元。"
            "更重要的是，如果美联储因通胀反弹而被迫加息，BTC可能腰斩至4万以下。"
            "黄金才是真正的避险资产，过去5000年从未归零。\n\n"
            "【中立观点  量化交易员】\n"
            "从纯技术面看，BTC月线RSI在72附近，尚未进入超买区域（>80）。"
            "成交量配合良好，但要警惕CME期货未平仓合约创新高带来的清算风险。"
            "建议采用网格策略，在8万-12万区间内分批操作，不追高不杀跌。"
        ),
        "expect": {
            "summary_level": "L1",
            "summary_desc": "应提取多空对立的核心逻辑，保留分歧点",
            "chunks": "1 个",
            "claims_min": 3,
            "claims_desc": "至少提取 3 条观点，应包含 bullish + bearish + neutral 不同 stance",
            "asset_scope_should_contain": ["BTC", "比特币"],
            "quality_grade_min": "B",
        },
    },
]


# ── AI 评估 System Prompt ────────────────────────────────────
_INGESTION_EVAL_SYSTEM = """你是 ThinkRoom 的**素材解析质量评审专家**。

系统的 Ingestion Pipeline 负责将用户上传的金融研究素材转化为结构化知识：
1. **文本拆分（Chunking）**：按段落切分长文本，每块 ≤32000 字符
2. **摘要生成（Summary）**：
   - L0（≤500字）：短文本直通，不调用 LLM
   - L1（≤6000字）：直接全文生成摘要（150-300字）
   - L2（≤60000字）：分段摘要 → 二次合并
   - L3（>60000字）：结构采样 → 一次生成
3. **观点抽取（Claim Extraction）**：从文本中提取结构化观点卡片，包含：
   summary, claim_type, stance, time_horizon, asset_scope, confidence 等字段
4. **质量评分（Quality Scoring）**：对每条 Claim 评分（A/B/C/D），含去重检测

你将收到多个测试样本的解析结果。请对每个样本逐项评估：

对于**每个样本**，请输出以下结构：

### 样本：[样本名称]

**1. 输入**
简述输入文本的特征（长度、主题、结构）

**2. 预期**
根据提供的预期说明，列出各环节应产生的结果

**3. 实际**
列出各环节的实际输出

**4. 评估结果**
给出该样本的评分（优秀/良好/合格/不合格）和简短结论

**5. 评估原因**
详细说明为什么给出这个评分，分析各环节的表现：
- 摘要是否准确反映原文核心逻辑？
- 观点提取是否完整？stance/asset_scope 等字段是否合理？
- 是否遗漏了重要观点？是否有过度提取（噪音观点）？

**6. 不足之处**
具体列出可改进的点（即使评为优秀也要找出优化空间）

---

最后给出**整体评估**：
- 综合评分（0-100）
- 各环节强弱项总结
- 优先改进建议（2-3条）

输出格式：中文，Markdown，结构清晰。"""


def _determine_summary_level(text_len: int) -> str:
    """根据文本长度判断预期的摘要级别"""
    if text_len <= 500:
        return "L0"
    elif text_len <= 6000:
        return "L1"
    elif text_len <= 60000:
        return "L2"
    return "L3"


def _run_one_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    """在独立 DB session 中执行单个样本测试（线程安全）"""
    from app.database import SessionLocal
    from app.workflows.ingestion_workflow import IngestionWorkflow
    from app.skills.claim_extraction_skill import ClaimExtractionSkill, ClaimExtractionInput
    from app.services.claim_quality import score_claim_quality

    _db = SessionLocal()
    try:
        return _run_sample_inner(sample, _db, IngestionWorkflow(_db), ClaimExtractionSkill())
    finally:
        _db.close()


def _run_sample_inner(sample, db, workflow, extraction_skill) -> Dict[str, Any]:
    """单样本全流程（Chunking → Summary → Extraction → Quality）"""
    from app.skills.claim_extraction_skill import ClaimExtractionInput
    from app.services.claim_quality import score_claim_quality

    sid = sample["id"]
    text = sample["text"]
    expect = sample["expect"]
    t0 = time.time()

    logger.info(f"[IngestionQualityTest] Running sample: {sid} ({len(text)} chars)")

    item: Dict[str, Any] = {
        "sample_id": sid,
        "sample_name": sample["name"],
        "input": {
            "text_length": len(text),
            "text_preview": text[:300],
            "full_text": text,
        },
        "expect": expect,
        "actual": {},
        "steps": [],
        "errors": [],
    }

    # ── Step 1: 文本拆分 ──
    try:
        t1 = time.time()
        chunks = workflow._split_chunks(text)
        dur1 = int((time.time() - t1) * 1000)
        item["actual"]["chunks_count"] = len(chunks)
        item["actual"]["chunks_sizes"] = [len(c) for c in chunks]
        item["steps"].append({
            "name": "文本拆分 (Chunking)",
            "duration_ms": dur1,
            "status": "ok",
            "output": {
                "chunks_count": len(chunks),
                "chunks_sizes": [len(c) for c in chunks],
            },
        })
    except Exception as e:
        item["errors"].append(f"文本拆分失败: {e}")
        item["steps"].append({"name": "文本拆分", "status": "error", "error": str(e)})
        chunks = [text]

    # ── Step 2: 摘要生成 ──
    try:
        t2 = time.time()
        summary = workflow._generate_article_summary(text)
        dur2 = int((time.time() - t2) * 1000)
        actual_level = _determine_summary_level(len(text))
        item["actual"]["summary"] = summary
        item["actual"]["summary_length"] = len(summary) if summary else 0
        item["actual"]["summary_level"] = actual_level
        item["steps"].append({
            "name": "摘要生成 (Summary)",
            "duration_ms": dur2,
            "status": "ok" if summary else "warning",
            "output": {
                "level": actual_level,
                "summary_length": len(summary) if summary else 0,
                "summary_preview": (summary or "")[:200],
            },
        })
    except Exception as e:
        item["errors"].append(f"摘要生成失败: {e}")
        item["steps"].append({"name": "摘要生成", "status": "error", "error": str(e)})
        summary = None

    # ── Step 3: 观点抽取（含重试） ──
    all_claims = []
    extraction_diag: list[str] = []  # 诊断信息
    MAX_EXTRACTION_RETRIES = 2
    try:
        t3 = time.time()
        for ci, chunk in enumerate(chunks):
            extraction_input = ClaimExtractionInput(
                raw_source_id=f"test_{sid}",
                text_content=chunk,
                speaker_hint=None,
                theme_hint=None,
            )
            # 带重试：如果首次返回 0 claims 且有 error，自动重试
            for attempt in range(1, MAX_EXTRACTION_RETRIES + 1):
                extraction_output = extraction_skill.execute(extraction_input)
                if extraction_output.error:
                    extraction_diag.append(f"chunk[{ci}] attempt {attempt}: {extraction_output.error}")
                    logger.warning(f"[IngestionQualityTest] {sid} chunk[{ci}] attempt {attempt} error: {extraction_output.error}")
                if extraction_output.warnings:
                    extraction_diag.extend(f"chunk[{ci}] warn: {w}" for w in extraction_output.warnings)
                if extraction_output.claims or not extraction_output.error:
                    break  # 成功或无错误，不再重试
                logger.info(f"[IngestionQualityTest] {sid} chunk[{ci}] retrying extraction (attempt {attempt + 1})...")
            for claim in extraction_output.claims:
                all_claims.append({
                    "summary": claim.summary,
                    "claim_type": claim.claim_type,
                    "stance": claim.stance,
                    "time_horizon": claim.time_horizon,
                    "asset_scope": claim.asset_scope,
                    "theme_scope": claim.theme_scope,
                    "confidence": claim.confidence,
                    "reasoning_basis": claim.reasoning_basis,
                    "conditions": claim.conditions,
                    "invalidation_conditions": claim.invalidation_conditions,
                })
        dur3 = int((time.time() - t3) * 1000)
        item["actual"]["claims_count"] = len(all_claims)
        item["actual"]["claims"] = all_claims
        all_assets = set()
        for c in all_claims:
            all_assets.update(c.get("asset_scope", []))
        item["actual"]["all_assets"] = sorted(all_assets)
        all_stances = set(c.get("stance", "") for c in all_claims)
        item["actual"]["all_stances"] = sorted(all_stances)
        # 将诊断信息加入 errors（便于前端展示）
        if not all_claims and extraction_diag:
            item["errors"].extend(extraction_diag)
        item["steps"].append({
            "name": "观点抽取 (Claim Extraction)",
            "duration_ms": dur3,
            "status": "ok" if all_claims else "warning",
            "output": {
                "claims_count": len(all_claims),
                "claims_summaries": [c["summary"][:60] for c in all_claims[:10]],
                "all_assets": sorted(all_assets),
                "all_stances": sorted(all_stances),
                "diagnostics": extraction_diag[:5] if extraction_diag else [],
            },
        })
    except Exception as e:
        item["errors"].append(f"观点抽取失败: {e}")
        item["steps"].append({"name": "观点抽取", "status": "error", "error": str(e)})

    # ── Step 4: 质量评分 ──
    quality_results = []
    try:
        t4 = time.time()
        for claim_dict in all_claims:
            q_input = {
                "summary": claim_dict["summary"],
                "claim_type": claim_dict["claim_type"],
                "stance": claim_dict["stance"],
                "time_horizon": claim_dict["time_horizon"],
                "asset_scope": claim_dict["asset_scope"],
                "reasoning_basis": claim_dict["reasoning_basis"],
                "conditions": claim_dict["conditions"],
                "invalidation_conditions": claim_dict["invalidation_conditions"],
                "confidence": claim_dict["confidence"],
                "full_quote": "",
                "trust_weight": 0.5,
            }
            quality = score_claim_quality(q_input)
            quality_results.append({
                "summary": claim_dict["summary"][:60],
                "grade": getattr(quality, "grade", "?"),
                "score": getattr(quality, "score", 0),
                "issues": getattr(quality, "issues", []),
            })
        dur4 = int((time.time() - t4) * 1000)
        item["actual"]["quality_results"] = quality_results
        grades = [q["grade"] for q in quality_results]
        item["actual"]["grades_distribution"] = {g: grades.count(g) for g in set(grades)}
        item["steps"].append({
            "name": "质量评分 (Quality Scoring)",
            "duration_ms": dur4,
            "status": "ok",
            "output": {
                "grades": grades,
                "distribution": item["actual"]["grades_distribution"],
            },
        })
    except Exception as e:
        item["errors"].append(f"质量评分失败: {e}")
        item["steps"].append({"name": "质量评分", "status": "error", "error": str(e)})

    item["total_duration_ms"] = int((time.time() - t0) * 1000)
    return item


def run_ingestion_quality_test(db) -> Dict[str, Any]:
    """执行素材解析质量测试，返回完整的测试数据（含 AI 评估）。
    
    3 个样本并发执行（各自独立 DB session），总耗时 ≈ 最慢样本时间。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_run_one_sample, sample): sample["id"] for sample in TEST_SAMPLES}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                sid = futures[future]
                logger.error(f"[IngestionQualityTest] Sample {sid} failed: {e}")
                results.append({
                    "sample_id": sid,
                    "sample_name": sid,
                    "input": {"text_length": 0, "text_preview": "", "full_text": ""},
                    "expect": {},
                    "actual": {},
                    "steps": [],
                    "errors": [str(e)],
                    "total_duration_ms": 0,
                })

    # 按 TEST_SAMPLES 原顺序排列结果
    order = {s["id"]: i for i, s in enumerate(TEST_SAMPLES)}
    results.sort(key=lambda r: order.get(r["sample_id"], 999))

    return {
        "test_time": datetime.now(timezone.utc).isoformat(),
        "samples_count": len(results),
        "results": results,
    }


def build_ai_eval_prompt(test_data: Dict[str, Any]) -> str:
    """将测试结果构建为 AI 评估的用户 prompt"""
    lines = []
    lines.append(f"# 素材解析质量测试结果")
    lines.append(f"测试时间: {test_data['test_time']}")
    lines.append(f"样本数量: {test_data['samples_count']}")
    lines.append("")

    for item in test_data["results"]:
        lines.append(f"\n---\n## 样本: {item['sample_name']} ({item['sample_id']})")
        lines.append(f"总耗时: {item['total_duration_ms']}ms")

        # 输入
        inp = item["input"]
        lines.append(f"\n### 输入文本")
        lines.append(f"- 长度: {inp['text_length']} 字符")
        lines.append(f"- 全文:\n```\n{inp['full_text']}\n```")

        # 预期
        exp = item["expect"]
        lines.append(f"\n### 预期结果")
        lines.append(f"- 摘要级别: {exp['summary_level']} — {exp['summary_desc']}")
        lines.append(f"- 分块: {exp['chunks']}")
        lines.append(f"- 观点数量: ≥{exp['claims_min']} — {exp['claims_desc']}")
        lines.append(f"- 资产覆盖应包含: {exp['asset_scope_should_contain']}")
        lines.append(f"- 质量评分下限: {exp['quality_grade_min']}")

        # 实际结果
        act = item["actual"]
        lines.append(f"\n### 实际结果")

        # 各步骤
        for step in item["steps"]:
            status_icon = "✓" if step["status"] == "ok" else "⚠" if step["status"] == "warning" else "✗"
            lines.append(f"\n**{status_icon} {step['name']}** ({step.get('duration_ms', 0)}ms)")
            if step.get("error"):
                lines.append(f"  错误: {step['error']}")
            if step.get("output"):
                for k, v in step["output"].items():
                    lines.append(f"  {k}: {_json.dumps(v, ensure_ascii=False, default=str)[:300]}")

        # 摘要全文
        if act.get("summary"):
            lines.append(f"\n**生成的摘要全文:**\n{act['summary']}")

        # 观点详情
        if act.get("claims"):
            lines.append(f"\n**提取的观点详情 ({act['claims_count']} 条):**")
            for i, c in enumerate(act["claims"]):
                lines.append(f"\n  观点 {i+1}:")
                lines.append(f"    summary: {c['summary']}")
                lines.append(f"    type: {c['claim_type']}, stance: {c['stance']}, horizon: {c['time_horizon']}")
                lines.append(f"    asset_scope: {c['asset_scope']}")
                lines.append(f"    confidence: {c['confidence']}")
                lines.append(f"    reasoning: {c.get('reasoning_basis', [])}")
                lines.append(f"    conditions: {c.get('conditions', [])}")
                lines.append(f"    invalidation: {c.get('invalidation_conditions', [])}")

        # 质量评分
        if act.get("quality_results"):
            lines.append(f"\n**质量评分:**")
            for q in act["quality_results"]:
                lines.append(f"  [{q['grade']}] {q['summary']} (score={q['score']})")
                if q.get("issues"):
                    lines.append(f"    issues: {q['issues']}")

        if item["errors"]:
            lines.append(f"\n**错误:**")
            for e in item["errors"]:
                lines.append(f"  ✗ {e}")

    return "\n".join(lines)


def build_copyable_report(test_data: Dict[str, Any], ai_evaluation: Optional[str] = None) -> str:
    """构建可复制的完整报告"""
    lines = []
    lines.append("=" * 60)
    lines.append("  素材解析质量测试报告")
    lines.append("=" * 60)
    lines.append(f"测试时间: {test_data['test_time']}")
    lines.append(f"样本数量: {test_data['samples_count']}")
    lines.append("")

    for item in test_data["results"]:
        lines.append("-" * 60)
        lines.append(f"【{item['sample_name']}】(耗时 {item['total_duration_ms']}ms)")
        lines.append("-" * 60)

        # 1. 输入
        inp = item["input"]
        lines.append(f"\n1. 输入:")
        lines.append(f"   文本长度: {inp['text_length']} 字符")
        lines.append(f"   预览: {inp['text_preview'][:150]}...")

        # 2. 预期
        exp = item["expect"]
        lines.append(f"\n2. 预期:")
        lines.append(f"   摘要: {exp['summary_level']} — {exp['summary_desc']}")
        lines.append(f"   分块: {exp['chunks']}")
        lines.append(f"   观点: ≥{exp['claims_min']}条 — {exp['claims_desc']}")
        lines.append(f"   资产覆盖: {exp['asset_scope_should_contain']}")

        # 3. 实际
        act = item["actual"]
        lines.append(f"\n3. 实际:")
        lines.append(f"   分块数: {act.get('chunks_count', '?')}, 大小: {act.get('chunks_sizes', [])}")
        lines.append(f"   摘要级别: {act.get('summary_level', '?')}, 长度: {act.get('summary_length', 0)}字")
        if act.get("summary"):
            lines.append(f"   摘要内容: {act['summary'][:200]}")
        lines.append(f"   观点数: {act.get('claims_count', 0)}")
        for i, c in enumerate(act.get("claims", [])):
            lines.append(f"     [{i+1}] {c['summary'][:80]}")
            lines.append(f"         stance={c['stance']}, assets={c['asset_scope']}, conf={c['confidence']}")
        if act.get("grades_distribution"):
            lines.append(f"   质量分布: {act['grades_distribution']}")

        # 步骤耗时
        lines.append(f"\n   步骤耗时:")
        for step in item["steps"]:
            icon = "✓" if step["status"] == "ok" else "⚠" if step["status"] == "warning" else "✗"
            lines.append(f"     {icon} {step['name']}: {step.get('duration_ms', 0)}ms")

        if item["errors"]:
            lines.append(f"\n   错误: {item['errors']}")

        lines.append("")

    if ai_evaluation:
        lines.append("=" * 60)
        lines.append("  AI 质量评审")
        lines.append("=" * 60)
        lines.append(ai_evaluation)

    return "\n".join(lines)
