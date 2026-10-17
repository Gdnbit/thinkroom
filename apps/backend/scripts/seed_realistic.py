"""清理重复/过时数据 + 插入完整合理的模拟数据

运行: python seed_realistic.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timezone, timedelta
from ulid import ULID
from sqlalchemy.orm import Session
from sqlalchemy import text, func

from app.database import SessionLocal
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.raw_source import RawSource
from app.schemas.speaker_profile import SpeakerProfile
from app.schemas.event_card import EventCard
from app.schemas.market_state_card import MarketStateCard
from app.schemas.flow_signal_card import FlowSignalCard
from app.schemas.setup_card import SetupCard
from app.schemas.strategy_card import StrategyCard
from app.schemas.briefing_card import BriefingCard
from app.schemas.lifecycle_log import LifecycleLog
from app.schemas.processing_trace import ProcessingTrace
from app.schemas.job_run import JobRun
from app.schemas.watchlist import Watchlist, WatchlistItem

now = datetime.now(timezone.utc)
db: Session = SessionLocal()

# ════════════════════════════════════════════════════════════════
#  Phase 1: 清理
# ════════════════════════════════════════════════════════════════
print("=" * 60)
print("Phase 1: 清理重复/过时数据")
print("=" * 60)

# 1a. 删除所有 claim 相关联表（cascade 依赖）
n = db.query(ClaimRawSourceLink).delete()
print(f"  ClaimRawSourceLink: 删除 {n}")
n = db.query(LifecycleLog).delete()
print(f"  LifecycleLog: 删除 {n}")
n = db.query(ProcessingTrace).delete()
print(f"  ProcessingTrace: 删除 {n}")

# 1b. 删除所有 claims（全部是重复测试数据）
n = db.query(ClaimCard).delete()
print(f"  ClaimCard: 删除 {n}")

# 1c. 删除测试 RawSource
n = db.query(RawSource).delete()
print(f"  RawSource: 删除 {n}")

# 1d. 删除测试 Speaker
n = db.query(SpeakerProfile).delete()
print(f"  SpeakerProfile: 删除 {n}")

# 1e. 删除空 Setup/Strategy/Briefing（全部是空标题的测试数据）
n = db.query(BriefingCard).delete()
print(f"  BriefingCard: 删除 {n}")
n = db.query(SetupCard).delete()
print(f"  SetupCard: 删除 {n}")
n = db.query(StrategyCard).delete()
print(f"  StrategyCard: 删除 {n}")

# 1f. 清理 JobRun（测试残留）
n = db.query(JobRun).delete()
print(f"  JobRun: 删除 {n}")

# 1g. MarketState: 每个 asset 只保留最新一条
print("  MarketStateCard: 去重...")
ms_kept = 0
for asset, in db.query(MarketStateCard.asset).distinct().all():
    latest = (db.query(MarketStateCard)
              .filter(MarketStateCard.asset == asset)
              .order_by(MarketStateCard.created_at.desc())
              .first())
    if latest:
        db.query(MarketStateCard).filter(
            MarketStateCard.asset == asset,
            MarketStateCard.id != latest.id
        ).delete(synchronize_session=False)
        ms_kept += 1
print(f"    保留 {ms_kept} 条（每资产最新 1 条），其余已删除")

# 1h. FlowSignal: 每个 asset+signal_type 只保留最新一条
print("  FlowSignalCard: 去重...")
fs_kept = 0
for asset, sig in db.query(FlowSignalCard.asset, FlowSignalCard.signal_type).distinct().all():
    latest = (db.query(FlowSignalCard)
              .filter(FlowSignalCard.asset == asset, FlowSignalCard.signal_type == sig)
              .order_by(FlowSignalCard.created_at.desc())
              .first())
    if latest:
        db.query(FlowSignalCard).filter(
            FlowSignalCard.asset == asset,
            FlowSignalCard.signal_type == sig,
            FlowSignalCard.id != latest.id
        ).delete(synchronize_session=False)
        fs_kept += 1
print(f"    保留 {fs_kept} 条（每类型最新 1 条），其余已删除")

# 1i. EventCard: 保留（都是真实 API 数据）
ev_count = db.query(EventCard).count()
print(f"  EventCard: 保留 {ev_count} 条（真实 API 数据）")

db.flush()
print()

# ════════════════════════════════════════════════════════════════
#  Phase 2: 插入合理模拟数据
# ════════════════════════════════════════════════════════════════
print("=" * 60)
print("Phase 2: 插入模拟数据")
print("=" * 60)

# ── Speakers ──────────────────────────────────────────────────
speakers = {}
SPEAKER_DATA = [
    {
        "name": "Ray Dalio",
        "aliases": ["达利欧", "瑞·达利欧", "Bridgewater"],
        "affiliation": "Bridgewater Associates",
        "role": "创始人 & 联席首席投资官",
        "domain_tags": ["宏观", "全球配置", "债务周期", "流动性"],
        "trust_weight": 0.85,
        "track_record_summary": "全球最大对冲基金创始人，擅长宏观经济周期分析。「全天候策略」创始者，对债务周期和流动性有深刻洞见。",
        "style_notes": "视野宏观，倾向长周期分析，常引用历史类比。对风险保持警惕，建议多元化配置。",
        "hit_rate": 0.68,
        "total_claims": 8,
        "verified_claims": 5,
    },
    {
        "name": "Arthur Hayes",
        "aliases": ["Hayes", "BitMEX创始人"],
        "affiliation": "Maelstrom Fund",
        "role": "首席投资官",
        "domain_tags": ["加密", "DeFi", "流动性", "美元"],
        "trust_weight": 0.72,
        "track_record_summary": "BitMEX 联合创始人，加密市场最具影响力的分析师之一。擅长从宏观流动性角度分析加密市场走势。",
        "style_notes": "文风犀利，善用类比。核心逻辑：全球流动性驱动加密市场定价。",
        "hit_rate": 0.62,
        "total_claims": 6,
        "verified_claims": 3,
    },
    {
        "name": "Cathie Wood",
        "aliases": ["木头姐", "ARK Invest", "Cathie"],
        "affiliation": "ARK Invest",
        "role": "CEO & 首席投资官",
        "domain_tags": ["科技", "创新", "BTC", "AI", "美股"],
        "trust_weight": 0.65,
        "track_record_summary": "ARK Invest 创始人，专注颠覆性创新投资。2020 年因 TSLA 封神，2022 年因重仓科技股回撤 75%。对 BTC 长期极度看多。",
        "style_notes": "极度乐观的长期主义者。常在别人恐惧时加仓，对 BTC 和 AI 有坚定信仰。",
        "hit_rate": 0.52,
        "total_claims": 5,
        "verified_claims": 2,
    },
    {
        "name": "中金公司研究部",
        "aliases": ["CICC", "中金", "中金研究"],
        "affiliation": "中国国际金融股份有限公司",
        "role": "卖方研究机构",
        "domain_tags": ["A股", "港股", "宏观", "策略", "行业"],
        "trust_weight": 0.70,
        "track_record_summary": "中国头部卖方机构，宏观与策略研究具有较高市场影响力。研报覆盖面广，数据扎实。",
        "style_notes": "风格稳健、措辞严谨。数据驱动，结论偏保守。",
        "hit_rate": 0.58,
        "total_claims": 4,
        "verified_claims": 2,
    },
    {
        "name": "PlanB",
        "aliases": ["100trillionUSD", "S2F模型作者"],
        "affiliation": "独立研究者",
        "role": "量化分析师 & KOL",
        "domain_tags": ["BTC", "量化模型", "S2F", "链上数据"],
        "trust_weight": 0.60,
        "track_record_summary": "Stock-to-Flow 模型创始人，BTC 定价框架最具影响力的分析师。模型在 2020-2021 牛市阶段高度准确，但 2022 后偏差加大。",
        "style_notes": "数据驱动，图表为主。对 BTC 极度看多，几乎不看空。",
        "hit_rate": 0.55,
        "total_claims": 5,
        "verified_claims": 2,
    },
]

for sd in SPEAKER_DATA:
    sp = SpeakerProfile(
        id=str(ULID()), name=sd["name"], aliases=sd["aliases"],
        affiliation=sd["affiliation"], role=sd["role"],
        domain_tags=sd["domain_tags"], trust_weight=sd["trust_weight"],
        track_record_summary=sd["track_record_summary"],
        style_notes=sd["style_notes"], hit_rate=sd["hit_rate"],
        total_claims=sd["total_claims"], verified_claims=sd["verified_claims"],
        status="active", created_at=now, updated_at=now,
    )
    db.add(sp)
    db.flush()
    speakers[sd["name"]] = sp.id

print(f"  SpeakerProfile: 插入 {len(speakers)} 条")

# ── RawSources ────────────────────────────────────────────────
sources = {}
SOURCE_DATA = [
    {
        "key": "src_dalio_liquidity",
        "source_type": "article",
        "title": "达利欧：全球流动性正在收紧，为经济寒冬做准备",
        "publisher": "LinkedIn / Bridgewater",
        "original_url": "https://www.linkedin.com/pulse/global-liquidity-tightening-ray-dalio",
        "speaker_hint": "Ray Dalio",
        "theme_hint": "流动性 美联储 全球宏观 债务周期",
        "language": "zh",
        "status": "completed",
        "word_count": 3200,
        "raw_text": (
            "全球流动性正在系统性收紧。美联储的量化紧缩（QT）仍在持续，每月缩表约 600 亿美元。"
            "与此同时，欧洲央行也在缩减资产负债表，日本央行虽然维持宽松但 YCC 调整信号频现。"
            "从历史角度看，我们正处于一个长期债务周期的下行阶段。在这样的环境下，"
            "现金和短期国债相对有吸引力，风险资产面临持续压力。\n\n"
            "需要特别关注的是，中国的信贷扩张可能成为全球流动性的边际变量。"
            "如果中国在 Q2 进一步宽松，可能部分对冲西方的紧缩效应，利好大宗商品和新兴市场。\n\n"
            "投资建议：保持防御性配置，增持黄金和通胀保护债券（TIPS），减持长久期风险资产。"
            "但要准备好在政策转向时快速调仓——历史表明，流动性拐点往往来得比预期更快。"
        ),
        "article_summary": (
            "【前提】美联储 QT 持续、欧日央行同步收紧，全球流动性进入系统性紧缩期。"
            "【逻辑链】长期债务周期下行 → 全球央行缩表 → 风险资产承压，但中国宽松可能成为边际变量。"
            "【结论】保持防御性配置，增持黄金和 TIPS。关注中国 Q2 信贷数据和全球政策拐点。"
            "【资产】黄金、TIPS、大宗商品、新兴市场 【时间】中期（3-6 个月）"
        ),
        "published_at": now - timedelta(days=2),
    },
    {
        "key": "src_hayes_btc",
        "source_type": "article",
        "title": "Arthur Hayes: 美元流动性决定 BTC 的下一步",
        "publisher": "Substack / Maelstrom",
        "original_url": "https://cryptohayes.substack.com/p/usd-liquidity-btc",
        "speaker_hint": "Arthur Hayes",
        "theme_hint": "BTC 美元流动性 RRP 财政部",
        "language": "zh",
        "status": "completed",
        "word_count": 4800,
        "raw_text": (
            "BTC 的价格本质上是全球美元流动性的衍生品。当美联储的逆回购（RRP）余额下降时，"
            "意味着流动性从美联储流向市场——这对 BTC 是最大的利好。\n\n"
            "目前 RRP 余额已从峰值 2.5 万亿降至约 4000 亿，释放了超过 2 万亿美元的流动性。"
            "同时，美国财政部的发债策略也在影响市场：短期 T-bill 发行增加 → 货币市场基金从 RRP 转向 T-bill → "
            "进一步释放流动性。\n\n"
            "关键变量：1）美联储何时停止 QT 2）财政部 TGA 账户余额变化 3）新一轮银行危机是否触发紧急流动性注入。\n\n"
            "我的判断：BTC 在 2026 年 Q2 将测试 10 万美元关口。如果美联储被迫降息或重启 QE，"
            "BTC 可能在年底前冲击 15 万。核心逻辑不变——印钞机一开，BTC 就涨。\n\n"
            "风险提示：如果美联储因通胀反弹被迫意外加息，短期可能出现 30% 以上的回调。"
            "但这种情况下 BTC 仍然是最好的对冲工具——它是唯一无法被央行稀释的硬资产。"
        ),
        "article_summary": (
            "【前提】RRP 余额从 2.5 万亿降至 4000 亿，释放超 2 万亿流动性；财政部 T-bill 发行策略进一步推升流动性。"
            "【逻辑链】美元流动性释放 → 风险资产受益 → BTC 作为「流动性衍生品」率先反应。"
            "【结论】BTC Q2 测试 10 万，年底可能冲击 15 万。前提是美联储不意外加息。"
            "【资产】BTC, 美元 【时间】中短期（Q2-Q4 2026）"
        ),
        "published_at": now - timedelta(days=1),
    },
    {
        "key": "src_cicc_a_share",
        "source_type": "report",
        "title": "中金2026年Q2 A股策略展望：结构性机会浮现",
        "publisher": "中国国际金融股份有限公司",
        "original_url": None,
        "speaker_hint": "中金公司研究部",
        "theme_hint": "A股 港股 策略 结构性机会 科技",
        "language": "zh",
        "status": "completed",
        "word_count": 6500,
        "raw_text": (
            "## 核心观点\n\n"
            "2026年Q2，A股整体仍处于震荡格局，但结构性机会明显增多。"
            "我们维持沪深300全年目标4800点（当前约4440），上行空间约8%。\n\n"
            "## 宏观环境\n\n"
            "国内方面，制造业PMI连续扩张，基建投资增速回升至两位数。"
            "但地产销售仍偏弱，社融增速放缓至9.5%。政策面上，预计Q2还有一次降准（50bp）。"
            "海外方面，美联储降息时点推迟，中美利差维持倒挂。人民币汇率在7.20-7.35区间波动。\n\n"
            "## 行业配置\n\n"
            "1. **超配：AI算力（服务器、光模块）** — 受益于全球AI资本开支持续增长\n"
            "2. **超配：高端制造（新能源车、机器人）** — 出口韧性+政策支持\n"
            "3. **标配：消费（白酒、医药）** — 估值合理但缺乏催化\n"
            "4. **低配：地产链** — 基本面尚未见底\n\n"
            "## 风险提示\n\n"
            "1. 美联储鹰派超预期导致全球风险资产调整\n"
            "2. 地缘政治风险升级（台海、南海）\n"
            "3. 国内经济复苏低于预期\n"
        ),
        "article_summary": (
            "【前提】制造业PMI扩张、基建投资回升，但地产偏弱、社融放缓。"
            "【逻辑链】结构性复苏 → AI算力和高端制造受益 → 指数震荡但个股机会多。"
            "【结论】沪深300目标4800，超配AI算力和高端制造，低配地产链。"
            "【资产】沪深300、CSI300、AI算力、新能源车 【时间】Q2 2026"
        ),
        "published_at": now - timedelta(hours=18),
    },
    {
        "key": "src_planb_s2f",
        "source_type": "tweet",
        "title": "PlanB: S2F 模型显示 BTC 被严重低估",
        "publisher": "Twitter/X",
        "original_url": "https://twitter.com/100trillionUSD/status/example",
        "speaker_hint": "PlanB",
        "theme_hint": "BTC S2F 减半 估值模型",
        "language": "en",
        "status": "completed",
        "word_count": 580,
        "raw_text": (
            "BTC S2F model update: current model price is $120K, actual price ~$70K. "
            "That's a 40% discount to fair value. Post-halving cycles historically take 12-18 months "
            "to reach model price. We're 6 months post-halving. Patience.\n\n"
            "Key data points:\n"
            "- S2F ratio now at 120 (post-halving)\n"
            "- LTH supply at ATH (78% of circulating supply)\n"
            "- Exchange balance at 5-year low\n"
            "- ETF inflows averaging $200M/day\n\n"
            "My target: $150K by end of 2026. If ETF demand stays at current pace + halving supply shock, "
            "could overshoot to $200K. NFA."
        ),
        "article_summary": (
            "S2F 模型显示 BTC 公允价格 12 万美元，当前 7 万实际价格折价 40%。"
            "减半后通常需 12-18 个月达到模型价格。链上数据（LTH 持仓创新高、交易所余额新低、ETF 日均流入 2 亿）全面看多。"
            "目标：2026 年底 15 万，乐观情况 20 万。"
        ),
        "published_at": now - timedelta(hours=8),
    },
]

for sd in SOURCE_DATA:
    src = RawSource(
        id=sd["key"], source_type=sd["source_type"], title=sd["title"],
        publisher=sd.get("publisher"), original_url=sd.get("original_url"),
        speaker_hint=sd["speaker_hint"], theme_hint=sd["theme_hint"],
        language=sd["language"], status=sd["status"],
        word_count=sd["word_count"], raw_text=sd["raw_text"],
        article_summary=sd["article_summary"],
        published_at=sd.get("published_at", now),
        ingested_at=now, created_at=now - timedelta(hours=3), updated_at=now,
    )
    db.add(src)
    sources[sd["key"]] = sd["key"]

db.flush()
print(f"  RawSource: 插入 {len(sources)} 条")

# ── Claims ────────────────────────────────────────────────────
CLAIM_DATA = [
    # --- 达利欧：全球流动性 ---
    {
        "summary": "全球流动性正在系统性收紧，美联储 QT 每月缩表约 600 亿美元",
        "claim_type": "macro_view", "stance": "bearish", "time_horizon": "medium",
        "asset_scope": ["美联储", "Fed", "DXY", "美元"],
        "theme_scope": ["流动性", "QT", "央行", "紧缩"],
        "confidence": 0.88,
        "reasoning_basis": ["美联储月缩表600亿", "欧洲央行同步缩表", "日本央行YCC调整信号"],
        "conditions": ["美联储维持QT节奏"],
        "invalidation_conditions": ["美联储宣布停止QT或重启QE"],
        "speaker": "Ray Dalio", "source": "src_dalio_liquidity",
    },
    {
        "summary": "中国 Q2 信贷扩张可能对冲西方紧缩，利好大宗商品和新兴市场",
        "claim_type": "macro_view", "stance": "conditional", "time_horizon": "short",
        "asset_scope": ["COPPER", "铜", "HSI", "恒生指数", "新兴市场", "EM"],
        "theme_scope": ["中国", "信贷", "流动性", "大宗商品"],
        "confidence": 0.65,
        "reasoning_basis": ["中国制造业PMI扩张", "预计Q2进一步宽松"],
        "conditions": ["中国Q2实际宽松力度超预期"],
        "invalidation_conditions": ["中国维持紧信用", "地产风险进一步暴露"],
        "speaker": "Ray Dalio", "source": "src_dalio_liquidity",
    },
    {
        "summary": "建议增持黄金和通胀保护债券（TIPS），作为防御性配置核心",
        "claim_type": "strategy_rule", "stance": "bullish", "time_horizon": "medium",
        "asset_scope": ["GOLD", "黄金", "TIPS", "通胀保护债券"],
        "theme_scope": ["避险", "配置", "防御"],
        "confidence": 0.82,
        "reasoning_basis": ["全球流动性紧缩", "长期债务周期下行", "通胀粘性"],
        "conditions": ["通胀维持在2%以上"],
        "invalidation_conditions": ["通胀快速回落至2%以下", "全球央行重启大规模宽松"],
        "speaker": "Ray Dalio", "source": "src_dalio_liquidity",
    },
    # --- Arthur Hayes: BTC + 流动性 ---
    {
        "summary": "BTC 本质是全球美元流动性的衍生品，RRP 下降是最大利好",
        "claim_type": "macro_view", "stance": "bullish", "time_horizon": "medium",
        "asset_scope": ["BTC", "比特币", "Bitcoin"],
        "theme_scope": ["流动性", "RRP", "美联储", "美元"],
        "confidence": 0.85,
        "reasoning_basis": ["RRP从2.5万亿降至4000亿", "释放超2万亿流动性", "财政部T-bill发行策略"],
        "conditions": ["美联储不意外大幅加息"],
        "invalidation_conditions": ["美联储因通胀反弹被迫加息50bp以上"],
        "speaker": "Arthur Hayes", "source": "src_hayes_btc",
    },
    {
        "summary": "BTC 2026 年 Q2 将测试 10 万美元，年底可能冲击 15 万",
        "claim_type": "asset_view", "stance": "bullish", "time_horizon": "medium",
        "asset_scope": ["BTC", "比特币", "Bitcoin"],
        "theme_scope": ["价格预测", "减半周期", "流动性"],
        "confidence": 0.75,
        "reasoning_basis": ["美元流动性持续释放", "减半供给冲击", "ETF持续流入"],
        "conditions": ["美联储维持或放松当前政策", "ETF资金持续流入"],
        "invalidation_conditions": ["美联储意外加息", "重大监管打击", "ETF大规模赎回"],
        "speaker": "Arthur Hayes", "source": "src_hayes_btc",
    },
    {
        "summary": "如果美联储因通胀反弹加息，BTC 短期可能回调 30% 以上",
        "claim_type": "risk_warning", "stance": "bearish", "time_horizon": "short",
        "asset_scope": ["BTC", "比特币", "Bitcoin"],
        "theme_scope": ["风险", "通胀", "美联储", "回调"],
        "confidence": 0.60,
        "reasoning_basis": ["通胀粘性可能超预期", "BTC杠杆率较高"],
        "conditions": ["通胀反弹迫使美联储加息"],
        "invalidation_conditions": ["通胀持续回落", "美联储维持降息路径"],
        "speaker": "Arthur Hayes", "source": "src_hayes_btc",
    },
    # --- 中金：A股策略 ---
    {
        "summary": "沪深300全年目标4800点，当前约4440，上行空间约8%",
        "claim_type": "asset_view", "stance": "bullish", "time_horizon": "medium",
        "asset_scope": ["CSI300", "沪深300", "A股"],
        "theme_scope": ["A股", "策略", "目标价"],
        "confidence": 0.68,
        "reasoning_basis": ["制造业PMI扩张", "基建投资增速回升", "估值处于历史中位数以下"],
        "conditions": ["经济复苏态势延续", "政策维持宽松"],
        "invalidation_conditions": ["经济二次探底", "外部冲击导致系统性风险"],
        "speaker": "中金公司研究部", "source": "src_cicc_a_share",
    },
    {
        "summary": "超配 AI 算力板块（服务器、光模块），受益于全球 AI 资本开支持续增长",
        "claim_type": "sector_view", "stance": "bullish", "time_horizon": "medium",
        "asset_scope": ["AI算力", "服务器", "光模块", "A股科技"],
        "theme_scope": ["AI", "算力", "资本开支", "科技"],
        "confidence": 0.78,
        "reasoning_basis": ["全球AI资本开支持续增长", "国产算力替代需求", "订单能见度高"],
        "conditions": ["AI资本开支维持增长"],
        "invalidation_conditions": ["AI投资泡沫破裂", "算力过剩导致价格战"],
        "speaker": "中金公司研究部", "source": "src_cicc_a_share",
    },
    {
        "summary": "低配地产链，基本面尚未见底",
        "claim_type": "sector_view", "stance": "bearish", "time_horizon": "medium",
        "asset_scope": ["地产", "房地产", "A股地产"],
        "theme_scope": ["地产", "房地产", "风险"],
        "confidence": 0.72,
        "reasoning_basis": ["地产销售仍偏弱", "社融增速放缓", "开发商信用风险未解"],
        "conditions": [],
        "invalidation_conditions": ["地产销售连续3个月环比改善", "重大需求端刺激政策出台"],
        "speaker": "中金公司研究部", "source": "src_cicc_a_share",
    },
    # --- PlanB: BTC S2F ---
    {
        "summary": "S2F 模型显示 BTC 公允价格 12 万美元，当前折价 40%",
        "claim_type": "data_observation", "stance": "bullish", "time_horizon": "medium",
        "asset_scope": ["BTC", "比特币", "Bitcoin"],
        "theme_scope": ["S2F", "估值模型", "减半"],
        "confidence": 0.70,
        "reasoning_basis": ["S2F比率120", "减半后供给冲击", "历史周期规律"],
        "conditions": ["S2F模型继续有效"],
        "invalidation_conditions": ["BTC价格持续18个月低于模型价50%以上"],
        "speaker": "PlanB", "source": "src_planb_s2f",
    },
    {
        "summary": "BTC 长期持有者（LTH）持仓占比创历史新高（78%），供给极度收紧",
        "claim_type": "data_observation", "stance": "bullish", "time_horizon": "short",
        "asset_scope": ["BTC", "比特币", "Bitcoin"],
        "theme_scope": ["链上数据", "LTH", "供给", "持仓"],
        "confidence": 0.90,
        "reasoning_basis": ["LTH持仓78%创ATH", "交易所余额5年新低", "ETF日均流入2亿"],
        "conditions": [],
        "invalidation_conditions": ["LTH大规模抛售", "交易所余额急剧回升"],
        "speaker": "PlanB", "source": "src_planb_s2f",
    },
    {
        "summary": "BTC 目标价 2026 年底 15 万美元，乐观情况可达 20 万",
        "claim_type": "asset_view", "stance": "bullish", "time_horizon": "medium",
        "asset_scope": ["BTC", "比特币", "Bitcoin"],
        "theme_scope": ["目标价", "S2F", "减半周期"],
        "confidence": 0.65,
        "reasoning_basis": ["S2F模型", "ETF需求", "减半供给冲击", "历史周期规律"],
        "conditions": ["ETF需求维持当前水平", "减半供给效应兑现"],
        "invalidation_conditions": ["全球流动性危机", "重大监管事件", "ETF大规模赎回"],
        "speaker": "PlanB", "source": "src_planb_s2f",
    },
]

claim_ids = []
for cd in CLAIM_DATA:
    speaker_id = speakers.get(cd.pop("speaker"))
    source_key = cd.pop("source")
    claim = ClaimCard(
        id=str(ULID()),
        summary=cd["summary"], full_quote=None,
        claim_type=cd["claim_type"], stance=cd["stance"],
        time_horizon=cd["time_horizon"],
        asset_scope=cd["asset_scope"], theme_scope=cd["theme_scope"],
        geographic_scope=[],
        conditions=cd["conditions"],
        invalidation_conditions=cd["invalidation_conditions"],
        reasoning_basis=cd["reasoning_basis"],
        confidence=cd["confidence"], source_trust_weight=0.7,
        is_contradicted=False, speaker_id=speaker_id,
        status="active",
        valid_until=now + timedelta(days=90),
        last_lifecycle_check=now,
        version=1, created_at=now - timedelta(hours=2), updated_at=now,
    )
    db.add(claim)
    db.flush()
    claim_ids.append(claim.id)
    # Link to source
    link = ClaimRawSourceLink(
        claim_id=claim.id, source_id=source_key,
        link_type="extracted_from", created_at=now, updated_at=now,
    )
    db.add(link)
    # Lifecycle log
    ll = LifecycleLog(
        id=str(ULID()), claim_id=claim.id,
        from_status="created", to_status="active",
        reason=f"Ingested from source {source_key}",
        triggered_by="ingestion_workflow",
        evidence_snapshot={"source_id": source_key},
        occurred_at=now, created_at=now, updated_at=now,
    )
    db.add(ll)

db.flush()
print(f"  ClaimCard: 插入 {len(claim_ids)} 条")

# ── Strategies ────────────────────────────────────────────────
STRATEGY_DATA = [
    {
        "strategy_name": "BTC 减半周期动量策略",
        "strategy_type": "trend_following",
        "summary": "基于 BTC 减半后供给冲击的历史规律，在减半后 6-18 个月内做多 BTC。结合链上数据（LTH、交易所余额、ETF流入）和技术面（均线、RSI）确认入场时机。",
        "core_logic": "减半 → 供给减少 → 12-18月内价格上涨 → 在周期高点（RSI月线>85、LTH大规模获利了结）逐步退出",
        "applicable_assets": ["BTC"],
        "applicable_horizons": ["medium", "long"],
        "applicable_regimes": ["牛市初期", "牛市中期"],
        "entry_conditions": ["BTC减半后6个月内", "LTH持仓占比>70%", "交易所余额持续下降", "价格站上200日均线"],
        "exit_rules": ["月线RSI>85", "LTH 30日净卖出>10万BTC", "价格跌破50日均线"],
        "risk_factors": ["美联储意外加息", "重大监管打击", "链上大户集中抛售"],
        "confidence": 0.75,
        "historical_win_rate": 0.67,
    },
    {
        "strategy_name": "宏观流动性轮动策略",
        "strategy_type": "macro_rotation",
        "summary": "根据全球流动性周期（央行资产负债表、RRP、信贷脉冲）在风险资产和避险资产间轮动。流动性扩张期增配 BTC/科技股，收缩期增配黄金/国债。",
        "core_logic": "流动性扩张 → 风险资产（BTC, NDX）; 流动性收缩 → 避险资产（GOLD, TIPS）; 中国信贷脉冲上行 → 大宗/新兴",
        "applicable_assets": ["BTC", "NDX", "GOLD", "TIPS", "COPPER", "HSI"],
        "applicable_horizons": ["medium"],
        "applicable_regimes": ["全场景"],
        "entry_conditions": ["流动性指标出现趋势变化", "信贷脉冲方向确认", "至少2个独立信号共振"],
        "exit_rules": ["流动性指标反转", "配置偏离度>15%触发再平衡"],
        "risk_factors": ["流动性指标滞后", "地缘政治事件突发", "多国央行政策分化"],
        "confidence": 0.70,
        "historical_win_rate": 0.60,
    },
    {
        "strategy_name": "A股AI算力主题策略",
        "strategy_type": "event_driven",
        "summary": "捕捉全球 AI 资本开支周期下，A 股算力产业链（服务器、光模块、芯片）的结构性机会。以订单数据和业绩预告为核心驱动。",
        "core_logic": "全球AI Capex增长 → 国产算力需求 → 业绩高增长 → 估值提升。重点关注订单能见度和毛利率趋势。",
        "applicable_assets": ["A股科技", "CSI300", "AI算力"],
        "applicable_horizons": ["short", "medium"],
        "applicable_regimes": ["AI资本开支上行期"],
        "entry_conditions": ["行业订单增速>30%", "龙头公司业绩预告超预期", "板块相对强弱指标上穿"],
        "exit_rules": ["订单增速放缓至<15%", "板块估值>历史75分位", "重大政策风险"],
        "risk_factors": ["AI投资泡沫破裂", "中美科技脱钩加剧", "算力过剩"],
        "confidence": 0.68,
        "historical_win_rate": 0.55,
    },
]

strategy_ids = []
for sd in STRATEGY_DATA:
    s = StrategyCard(
        id=str(ULID()),
        strategy_name=sd["strategy_name"], strategy_type=sd["strategy_type"],
        summary=sd["summary"], core_logic=sd["core_logic"],
        applicable_assets=sd["applicable_assets"],
        applicable_horizons=sd["applicable_horizons"],
        applicable_regimes=sd["applicable_regimes"],
        entry_conditions=sd["entry_conditions"],
        disqualifying_conditions=[],
        exit_rules=sd["exit_rules"], risk_factors=sd["risk_factors"],
        position_sizing_notes=None,
        historical_win_rate=sd["historical_win_rate"],
        confidence=sd["confidence"],
        last_validated_at=now,
        status="active", version=1, distilled_by="strategy_distillation_skill",
        created_at=now - timedelta(hours=1), updated_at=now,
    )
    db.add(s)
    db.flush()
    strategy_ids.append(s.id)

print(f"  StrategyCard: 插入 {len(strategy_ids)} 条")

# ── SetupCards ────────────────────────────────────────────────
SETUP_DATA = [
    {
        "strategy_idx": 0,  # BTC 减半周期动量
        "asset": "BTC", "timeframe": "1d", "direction": "long",
        "strategy_fit_score": 0.82, "evidence_strength_score": 0.78, "overall_score": 0.80,
        "matched_conditions": ["BTC减半后6个月内 ✓", "LTH持仓>70% (78%) ✓", "交易所余额持续下降 ✓", "价格站上200日均线 ✓"],
        "unmatched_conditions": [],
        "recommended_action": "enter",
        "action_notes": "所有入场条件满足。建议分 3 批入场（当前价位 40%、回调至 65000 加 30%、回调至 60000 加 30%）。止损设在 55000（-20%）。",
        "invalidation_conditions": ["BTC跌破200日均线", "LTH 30日净卖出>5万BTC"],
    },
    {
        "strategy_idx": 1,  # 宏观流动性轮动
        "asset": "GOLD", "timeframe": "1w", "direction": "long",
        "strategy_fit_score": 0.75, "evidence_strength_score": 0.80, "overall_score": 0.77,
        "matched_conditions": ["全球流动性收紧信号 ✓", "美联储QT持续 ✓"],
        "unmatched_conditions": ["信贷脉冲信号尚不明确"],
        "recommended_action": "enter",
        "action_notes": "流动性收缩周期利好黄金。建议配置总资产的 15-20%。近期突破历史新高，但 RSI 偏高，可等待回调至 4500 附近加仓。",
        "invalidation_conditions": ["美联储转鸽并重启QE", "通胀快速回落至1.5%以下"],
    },
]

setup_ids = []
for sd in SETUP_DATA:
    s = SetupCard(
        id=str(ULID()),
        strategy_id=strategy_ids[sd["strategy_idx"]],
        asset=sd["asset"], timeframe=sd["timeframe"], direction=sd["direction"],
        strategy_fit_score=sd["strategy_fit_score"],
        evidence_strength_score=sd["evidence_strength_score"],
        overall_score=sd["overall_score"],
        matched_conditions=sd["matched_conditions"],
        unmatched_conditions=sd["unmatched_conditions"],
        disqualifying_flags=[],
        recommended_action=sd["recommended_action"],
        action_notes=sd["action_notes"],
        invalidation_conditions=sd["invalidation_conditions"],
        supporting_claim_ids=claim_ids[:4],
        supporting_event_ids=[],
        market_state_snapshot={},
        flow_snapshot={},
        status="active",
        evaluated_at=now,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=30), updated_at=now,
    )
    db.add(s)
    db.flush()
    setup_ids.append(s.id)

print(f"  SetupCard: 插入 {len(setup_ids)} 条")

# ── BriefingCards ─────────────────────────────────────────────
BRIEFING_DATA = [
    {
        "title": "BTC Q2 多头机会研究简报",
        "topic": "BTC",
        "question": "当前 BTC 是否具备做多条件？",
        "direction": "bullish", "setup_quality": "high", "time_horizon": "medium",
        "asset_scope": ["BTC", "比特币"],
        "executive_summary": (
            "综合多方证据，BTC 当前具备较强的做多条件。减半后供给冲击叠加美元流动性释放，"
            "链上数据全面看多（LTH 持仓创 ATH、交易所余额新低、ETF 日均流入 2 亿美元）。"
            "S2F 模型显示当前折价 40%。技术面处于上升通道，200 日均线上方运行。"
            "主要风险：美联储因通胀反弹被迫加息、重大监管事件。\n\n"
            "建议：分批建仓做多 BTC，目标 10-15 万美元（Q2-Q4），止损 55000 美元。"
        ),
        "supporting_points": [
            "RRP从2.5万亿降至4000亿，释放2万亿流动性（Arthur Hayes）",
            "LTH持仓占比78%创ATH，供给极度收紧（PlanB）",
            "S2F模型显示公允价格12万，当前折价40%（PlanB）",
            "ETF日均流入2亿美元，机构需求持续（数据观察）",
        ],
        "contrary_points": [
            "美联储QT仍在持续，每月缩表600亿（Ray Dalio）",
            "通胀粘性可能超预期，美联储存在意外加息风险（Arthur Hayes）",
            "S2F模型在2022年后偏差加大，模型有效性存疑",
        ],
        "key_risks": [
            "美联储因通胀反弹加息50bp以上 → BTC可能回调30%+",
            "重大监管打击（美国SEC执法行动）",
            "链上大户集中抛售触发清算链",
        ],
        "recommended_action": "enter",
        "setup_idx": 0,
        "model_used": "anthropic/claude-sonnet-4.6",
        "committee_level": "committee",
        "generation_time_ms": 12500,
    },
    {
        "title": "黄金避险配置研究简报",
        "topic": "GOLD",
        "question": "当前宏观环境下黄金的配置价值如何？",
        "direction": "bullish", "setup_quality": "medium", "time_horizon": "medium",
        "asset_scope": ["GOLD", "黄金"],
        "executive_summary": (
            "全球流动性系统性收紧背景下，黄金作为避险资产具备较强配置价值。"
            "驱动因素：央行购金潮（2023-2025 年连续 3 年超 1000 吨）、地缘政治风险溢价、"
            "通胀粘性支撑实际需求。当前价格已突破历史新高，技术面强势但 RSI 偏高。\n\n"
            "注意：美联储若转鸽并重启 QE，流动性涌入风险资产可能削弱黄金的相对吸引力。"
            "建议配置总资产的 15-20%，以定投方式建仓，避免追高。"
        ),
        "supporting_points": [
            "全球央行连续3年购金超1000吨，去美元化趋势（数据观察）",
            "美联储QT持续 + 通胀粘性 = 实际利率上行空间有限（Ray Dalio）",
            "地缘政治风险溢价：中东、台海、俄乌（多源）",
        ],
        "contrary_points": [
            "美元走强限制黄金上行空间（Ray Dalio）",
            "RSI偏高，短期存在技术性回调风险",
            "美联储转鸽后资金可能转向风险资产",
        ],
        "key_risks": [
            "美联储突然转鸽，大量资金从避险转向风险资产",
            "通胀快速回落，实际利率大幅上行",
            "央行购金节奏放缓",
        ],
        "recommended_action": "enter",
        "setup_idx": 1,
        "model_used": "anthropic/claude-sonnet-4.6",
        "committee_level": "committee",
        "generation_time_ms": 10800,
    },
]

for bd in BRIEFING_DATA:
    setup_idx = bd.pop("setup_idx")
    b = BriefingCard(
        id=str(ULID()),
        title=bd["title"], topic=bd["topic"], question=bd["question"],
        direction=bd["direction"], setup_quality=bd["setup_quality"],
        time_horizon=bd["time_horizon"], asset_scope=bd["asset_scope"],
        executive_summary=bd["executive_summary"],
        supporting_points=bd["supporting_points"],
        contrary_points=bd["contrary_points"],
        key_risks=bd["key_risks"],
        strategy_fit_summary=None,
        recommended_action=bd["recommended_action"],
        invalidation_conditions=[],
        setup_id=setup_ids[setup_idx],
        source_claim_ids=claim_ids[:6],
        source_event_ids=[],
        model_used=bd["model_used"],
        committee_level=bd["committee_level"],
        generation_time_ms=bd["generation_time_ms"],
        token_count=2500,
        status="active",
        generated_at=now,
        created_at=now - timedelta(minutes=15), updated_at=now,
    )
    db.add(b)

db.flush()
print(f"  BriefingCard: 插入 {len(BRIEFING_DATA)} 条")

# ── Watchlists ────────────────────────────────────────────────
wl = Watchlist(
    id=str(ULID()),
    name="核心资产观察", description="跟踪的核心资产列表",
    list_type="asset", status="active", config={},
    created_at=now, updated_at=now,
)
db.add(wl)
db.flush()

WATCHLIST_ITEMS = [
    {"item_key": "BTC", "display_name": "比特币", "priority": 1, "notes": "减半周期跟踪，目标 10-15 万"},
    {"item_key": "GOLD", "display_name": "黄金", "priority": 2, "notes": "避险配置核心，关注央行购金数据"},
    {"item_key": "ETH", "display_name": "以太坊", "priority": 3, "notes": "Pectra 升级催化，关注 DeFi TVL"},
    {"item_key": "CSI300", "display_name": "沪深300", "priority": 4, "notes": "A股结构性机会，关注 AI 算力"},
    {"item_key": "DXY", "display_name": "美元指数", "priority": 5, "notes": "全球流动性风向标"},
]
for wi in WATCHLIST_ITEMS:
    item = WatchlistItem(
        id=str(ULID()), watchlist_id=wl.id,
        item_type="asset", item_key=wi["item_key"],
        display_name=wi["display_name"], priority=wi["priority"],
        notes=wi["notes"], config={}, status="active",
        created_at=now, updated_at=now,
    )
    db.add(item)

db.flush()
print(f"  Watchlist: 1 条 + {len(WATCHLIST_ITEMS)} 个关注项")

# ── 补充事件类型多样性 ──────────────────────────────────────
# 现有 10 条都是 news 类型，补充几条不同类型
EXTRA_EVENTS = [
    {
        "event_type": "central_bank",
        "title": "美联储维持利率不变，点阵图暗示年内降息2次",
        "summary": "美联储3月议息会议维持联邦基金利率在5.25-5.50%不变，符合预期。点阵图中位数显示年内可能降息2次（50bp），但删除了「很快」降息的措辞。鲍威尔在记者会上强调数据依赖。",
        "importance": 0.92, "impact_direction": "neutral", "time_horizon": "medium",
        "asset_scope": ["DXY", "SPX", "BTC", "GOLD"],
        "theme_scope": ["美联储", "利率", "降息"],
    },
    {
        "event_type": "macro",
        "title": "中国3月PMI 50.8 超预期，制造业连续扩张",
        "summary": "中国3月制造业PMI录得50.8，高于预期50.1和前值49.1，连续2个月处于扩张区间。新订单和出口订单指数均改善。",
        "importance": 0.75, "impact_direction": "bullish", "time_horizon": "short",
        "asset_scope": ["HSI", "CSI300", "COPPER"],
        "theme_scope": ["中国", "PMI", "制造业"],
    },
    {
        "event_type": "crypto_onchain",
        "title": "BTC 交易所余额降至5年新低，长期持有者持仓创新高",
        "summary": "Glassnode数据显示BTC交易所余额降至238万枚（5年新低），同时长期持有者（>155天）持仓占比升至78%创历史新高。供给收紧信号明显。",
        "importance": 0.80, "impact_direction": "bullish", "time_horizon": "short",
        "asset_scope": ["BTC"],
        "theme_scope": ["链上数据", "供给", "LTH"],
    },
    {
        "event_type": "geopolitical",
        "title": "中东局势紧张推升油价，布伦特原油突破110美元",
        "summary": "红海航运受阻加剧，胡塞武装袭击频率上升。布伦特原油突破110美元/桶，市场担忧供给中断风险。",
        "importance": 0.78, "impact_direction": "bearish", "time_horizon": "short",
        "asset_scope": ["OIL", "GOLD"],
        "theme_scope": ["地缘政治", "石油", "中东"],
    },
]

for ev in EXTRA_EVENTS:
    e = EventCard(
        id=str(ULID()),
        event_type=ev["event_type"], title=ev["title"], summary=ev["summary"],
        source_name="模拟数据",
        event_timestamp=now - timedelta(hours=6),
        asset_scope=ev["asset_scope"], theme_scope=ev["theme_scope"],
        geographic_scope=[], importance=ev["importance"],
        impact_direction=ev["impact_direction"], time_horizon=ev["time_horizon"],
        is_surprise=False, status="active",
        extra_data={"seeded_by": "seed_realistic"},
        created_at=now, updated_at=now,
    )
    db.add(e)

db.flush()
print(f"  EventCard: 新增 {len(EXTRA_EVENTS)} 条（补充事件类型多样性）")

# ════════════════════════════════════════════════════════════════
#  Commit
# ════════════════════════════════════════════════════════════════
db.commit()
print()
print("=" * 60)
print("完成! 最终数据统计:")
print("=" * 60)
print(f"  SpeakerProfile: {db.query(SpeakerProfile).count()}")
print(f"  RawSource:      {db.query(RawSource).count()}")
print(f"  ClaimCard:      {db.query(ClaimCard).count()}")
print(f"  StrategyCard:   {db.query(StrategyCard).count()}")
print(f"  SetupCard:      {db.query(SetupCard).count()}")
print(f"  BriefingCard:   {db.query(BriefingCard).count()}")
print(f"  EventCard:      {db.query(EventCard).count()}")
print(f"  MarketStateCard:{db.query(MarketStateCard).count()}")
print(f"  FlowSignalCard: {db.query(FlowSignalCard).count()}")
print(f"  Watchlist:      {db.query(Watchlist).count()}")
print(f"  WatchlistItem:  {db.query(WatchlistItem).count()}")
db.close()
