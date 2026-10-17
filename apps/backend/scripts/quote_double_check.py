"""
Quote Double Check — 对 NOT_FOUND 的 full_quote 进行二次校验

思路：
  1. 找出所有 grounding 失败（NOT_FOUND）的 claim
  2. 将 claim.summary + claim.full_quote + source.raw_text（截取相关段落）
     发送给 LLM，要求返回**原文中的逐字引用**
  3. 对 LLM 返回的新 quote 重新 grounding
  4. 如果匹配成功，更新 claim.full_quote

优势：
  - 只对失败的 ~38 条执行，不重跑整个 pipeline
  - 使用廉价模型 (gemini-flash)，成本极低
  - LLM 直接看到原文，比首次提取时更有针对性
"""
import sys
import time
import json

sys.path.insert(0, ".")

from app.database import SessionLocal, safe_commit
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.raw_source import RawSource
from app.services.quote_grounding import ground_quote, GroundingResult
from app.utils.llm_client import LLMClient
from loguru import logger

# ── 配置 ──
GROUNDING_THRESHOLD = 0.75
# 发送给 LLM 的原文上下文窗口（字符数），避免 token 超限
CONTEXT_WINDOW = 6000

SYSTEM_PROMPT = """你是一个精确引用提取助手。

用户会给你：
1. 一段观点摘要 (summary)
2. 一段之前提取的引用 (old_quote)，但这段引用在原文中找不到精确匹配
3. 原文内容 (source_text)

你的任务是：从原文中找到最能支撑这个观点的**一段连续文字**，逐字复制出来。

严格要求：
- 你输出的内容必须是原文中**逐字连续**的片段，一个字都不能改
- 不要跨段落/跨消息拼接
- 不要用省略号 (……/...) 连接不同段落
- 不要改写、缩写、重组原文
- 如果是群聊记录，只引用同一条消息内的文字
- 引用长度控制在 30-200 字
- 如果确实找不到合适的原文片段，返回空字符串 ""

只输出引用文本本身，不要加任何前缀、解释或 markdown 格式。"""


def _find_relevant_context(source_text: str, old_quote: str, summary: str) -> str:
    """从原文中截取与 old_quote 最相关的上下文窗口"""
    import difflib

    if len(source_text) <= CONTEXT_WINDOW:
        return source_text

    # 用 old_quote 的关键词在原文中定位大致位置
    # 取 old_quote 中最长的连续中文子串作为锚点
    import re
    chunks = re.findall(r'[\u4e00-\u9fffa-zA-Z0-9]{4,}', old_quote)
    best_pos = 0
    for chunk in sorted(chunks, key=len, reverse=True)[:5]:
        idx = source_text.find(chunk)
        if idx != -1:
            best_pos = idx
            break

    # 以 best_pos 为中心，截取上下文窗口
    half = CONTEXT_WINDOW // 2
    start = max(0, best_pos - half)
    end = min(len(source_text), start + CONTEXT_WINDOW)
    start = max(0, end - CONTEXT_WINDOW)

    context = source_text[start:end]

    # 加上边界标记
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(source_text) else ""
    return prefix + context + suffix


def main():
    db = SessionLocal()
    llm = LLMClient()

    print("=" * 60)
    print("  Quote Double Check — 二次校验 NOT_FOUND 引用")
    print("=" * 60)

    # Step 1: 找出所有 NOT_FOUND 的 claim
    claims = db.query(ClaimCard).all()
    not_found_items = []

    for c in claims:
        lk = db.query(ClaimRawSourceLink).filter(
            ClaimRawSourceLink.claim_id == c.id
        ).first()
        if not lk:
            continue
        src = db.query(RawSource).filter(RawSource.id == lk.source_id).first()
        if not src or not src.raw_text or not c.full_quote:
            continue
        gr = ground_quote(src.raw_text, c.full_quote)
        if not gr.is_grounded:
            not_found_items.append((c, src, gr))

    print(f"\n  总 Claims: {len(claims)}")
    print(f"  NOT_FOUND: {len(not_found_items)}")
    print(f"  将对这 {len(not_found_items)} 条进行 double check\n")

    if not not_found_items:
        print("  ✅ 没有需要 double check 的 claim")
        db.close()
        return

    # Step 2: 逐条 double check
    fixed = 0
    still_failed = 0
    errors = 0

    for i, (claim, source, old_gr) in enumerate(not_found_items, 1):
        print(f"── [{i}/{len(not_found_items)}] {claim.summary[:50]} (score={old_gr.score:.2f})")

        # 截取相关上下文
        context = _find_relevant_context(source.raw_text, claim.full_quote, claim.summary)

        user_prompt = f"""观点摘要: {claim.summary}

之前提取的引用 (未能在原文中精确匹配):
{claim.full_quote}

原文内容:
{context}

请从上面的原文中找到最能支撑这个观点的一段连续原文，逐字复制出来。"""

        try:
            new_quote = llm.complete_with_role_str(
                skill_role="extraction",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=512,
                temperature=0.0,
            )

            # 清理 LLM 输出
            new_quote = (new_quote or "").strip()
            # 去除可能的引号包裹
            if new_quote.startswith('"') and new_quote.endswith('"'):
                new_quote = new_quote[1:-1]
            if new_quote.startswith('"') and new_quote.endswith('"'):
                new_quote = new_quote[1:-1]

            if not new_quote or len(new_quote) < 10:
                print(f"    ⚠ LLM 返回空或太短，跳过")
                still_failed += 1
                continue

            # 重新 grounding
            new_gr = ground_quote(source.raw_text, new_quote)
            status_str = f"{new_gr.status.value} score={new_gr.score:.2f}"

            if new_gr.is_grounded:
                # 成功！更新 claim
                old_quote = claim.full_quote
                claim.full_quote = new_gr.grounded_quote
                safe_commit(db)
                fixed += 1
                print(f"    ✅ 修复成功 ({status_str})")
                print(f"       旧: {old_quote[:80]}...")
                print(f"       新: {new_gr.grounded_quote[:80]}...")
            else:
                still_failed += 1
                print(f"    ❌ 仍未匹配 ({status_str})")
                print(f"       LLM返回: {new_quote[:80]}...")

        except Exception as e:
            errors += 1
            logger.error(f"    ⚠ 错误: {e}")
            print(f"    ⚠ 错误: {e}")

        # 避免 API 速率限制
        time.sleep(0.5)

    # Step 3: 最终统计
    print("\n" + "=" * 60)
    print("  Double Check 结果")
    print("=" * 60)
    print(f"  处理: {len(not_found_items)}")
    print(f"  ✅ 修复: {fixed}")
    print(f"  ❌ 仍失败: {still_failed}")
    print(f"  ⚠ 错误: {errors}")
    print(f"  修复率: {fixed}/{len(not_found_items)} ({100*fixed/max(1,len(not_found_items)):.1f}%)")

    # 重新计算整体 grounding 率
    total_grounded = 0
    total_claims = 0
    for c in db.query(ClaimCard).all():
        lk = db.query(ClaimRawSourceLink).filter(
            ClaimRawSourceLink.claim_id == c.id
        ).first()
        if not lk:
            continue
        src = db.query(RawSource).filter(RawSource.id == lk.source_id).first()
        if not src or not src.raw_text or not c.full_quote:
            continue
        total_claims += 1
        gr = ground_quote(src.raw_text, c.full_quote)
        if gr.is_grounded:
            total_grounded += 1

    print(f"\n  📊 整体 Grounding 率: {total_grounded}/{total_claims} ({100*total_grounded/max(1,total_claims):.1f}%)")
    print(f"     (之前: 126/164 = 76.8%)")

    db.close()
    print("\n═══ 完成 ═══")


if __name__ == "__main__":
    main()
