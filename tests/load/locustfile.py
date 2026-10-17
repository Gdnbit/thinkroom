"""P2-D · 负载测试入口（locustfile）

设计原则
--------
- **优先 read-only 通路**：负载测试目的是验证并发下的健康度（连接池、锁、event loop），
  不是验证 LLM 调用速度。涉及 LLM 的写入路径（chat / briefing 生成 / debate）默认关闭，
  通过环境变量 `LOAD_INCLUDE_WRITES=1` 可显式开启
- **真实场景权重**：参考生产流量分布
  - HealthCheck：35%（k8s probe 高频）
  - Watchlists：20%（首页 list）
  - Claims：20%（asset 详情页）
  - Briefings：10%（briefing 列表）
  - Snapshots：10%（market overview）
  - Search：5%（语义搜索）
- **认证**：从环境变量读 `APP_API_KEY`，自动加 `X-API-Key` header
- **错误容忍**：404/数据为空不算失败（生产开局可能空库），仅 5xx 计入失败率

跑法
----
    pip install -r tests/load/requirements.txt

    # WebUI 模式（推荐做交互式调优）
    locust -f tests/load/locustfile.py --host http://localhost:8000

    # Headless（CI 跑 baseline）
    locust -f tests/load/locustfile.py --headless \\
        -u 50 -r 5 -t 5m --host http://localhost:8000 \\
        --csv reports/load --html reports/load.html

    # 含写入路径
    LOAD_INCLUDE_WRITES=1 locust -f tests/load/locustfile.py --host http://localhost:8000

环境变量
--------
- `LOAD_HOST`：默认 http://localhost:8000
- `LOAD_API_KEY`：覆盖 X-API-Key header；空则不加（dev 模式）
- `LOAD_INCLUDE_WRITES`：1=开启写入场景（POST /raw-sources / /observations）
- `LOAD_FAIL_ON_5XX_ONLY`：默认 1=只把 5xx 算失败；设 0 时 4xx 也算

退出码
------
- 0：通过（fail rate < 5%）
- 1：失败率超阈值
"""
from __future__ import annotations

import os
import random
from typing import Optional

from locust import HttpUser, between, events, task
from locust.env import Environment


# ── 配置 ──────────────────────────────────────────────────────────


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


_API_KEY = os.environ.get("LOAD_API_KEY", "").strip()
_INCLUDE_WRITES = _bool_env("LOAD_INCLUDE_WRITES", False)
_FAIL_ON_5XX_ONLY = _bool_env("LOAD_FAIL_ON_5XX_ONLY", True)
_FAIL_RATE_THRESHOLD = 0.05


# ── 共享请求 helper ──────────────────────────────────────────────


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if _API_KEY:
        h["X-API-Key"] = _API_KEY
    return h


def _req(client, method: str, path: str, name: Optional[str] = None, **kwargs):
    """统一请求包装：注入 header + 错误标记 + 命名（路径 group）.

    - 4xx + LOAD_FAIL_ON_5XX_ONLY=1：标记 success（404/422 在空库下常见，不算失败）
    - 5xx：始终算失败
    """
    headers = kwargs.pop("headers", {})
    headers.update(_headers())
    name = name or path
    with client.request(method, path, name=name, headers=headers, catch_response=True, **kwargs) as r:
        if r.status_code >= 500:
            r.failure(f"{method} {name} → {r.status_code}: {r.text[:120]}")
        elif r.status_code >= 400 and not _FAIL_ON_5XX_ONLY:
            r.failure(f"{method} {name} → {r.status_code}")
        else:
            r.success()


# ── 用户场景 ──────────────────────────────────────────────────────


class HealthUser(HttpUser):
    """K8s probe 高频访问 + Prometheus scrape — 应该极快无副作用."""

    wait_time = between(0.2, 1.0)
    weight = 35

    @task(5)
    def liveness(self):
        _req(self.client, "GET", "/health/live")

    @task(3)
    def readiness(self):
        _req(self.client, "GET", "/health/ready")

    @task(1)
    def metrics(self):
        # /metrics 不在 /api/v1 前缀下；不需 X-API-Key
        with self.client.get("/metrics", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"/metrics → {r.status_code}")
            else:
                r.success()


class WatchlistUser(HttpUser):
    """前端首页 list — read-only，DB query 一波带走."""

    wait_time = between(1, 3)
    weight = 20

    def on_start(self):
        # 拉一次 watchlist 列表，缓存 ID 用于 detail 请求
        self._ids: list[str] = []
        with self.client.get("/api/v1/watchlists", headers=_headers(),
                             catch_response=True, name="GET /watchlists [bootstrap]") as r:
            if r.status_code == 200:
                try:
                    self._ids = [w.get("id") for w in r.json() if w.get("id")]
                except Exception:
                    pass
            r.success()  # bootstrap 无所谓失败

    @task(3)
    def list_watchlists(self):
        _req(self.client, "GET", "/api/v1/watchlists", name="GET /watchlists")

    @task(2)
    def get_watchlist(self):
        if not self._ids:
            return
        wid = random.choice(self._ids)
        _req(self.client, "GET", f"/api/v1/watchlists/{wid}",
             name="GET /watchlists/{id}")


class ClaimsUser(HttpUser):
    """资产详情页 - claim_cards 列表查询."""

    wait_time = between(1, 3)
    weight = 20

    @task(2)
    def list_btc(self):
        _req(self.client, "GET",
             "/api/v1/claims/?asset=BTC&limit=20",
             name="GET /claims (BTC)")

    @task(1)
    def list_eth(self):
        _req(self.client, "GET",
             "/api/v1/claims/?asset=ETH&limit=20",
             name="GET /claims (ETH)")

    @task(1)
    def list_filtered(self):
        # 多 filter 走更复杂查询路径
        _req(self.client, "GET",
             "/api/v1/claims/?asset=BTC&stance=bullish&time_horizon=short&limit=10",
             name="GET /claims (filtered)")


class BriefingsUser(HttpUser):
    """Briefing 列表（已生成的，不触发 LLM 重算）."""

    wait_time = between(2, 5)
    weight = 10

    @task
    def list_briefings(self):
        _req(self.client, "GET",
             "/api/v1/briefings/?limit=10",
             name="GET /briefings")


class SnapshotsUser(HttpUser):
    """Market snapshot — read-only，可能命中缓存."""

    wait_time = between(2, 5)
    weight = 10

    @task
    def latest_snapshot(self):
        _req(self.client, "GET",
             "/api/v1/snapshots/latest",
             name="GET /snapshots/latest")


class SearchUser(HttpUser):
    """语义搜索 — 涉及 embedding 计算，是延迟敏感场景."""

    wait_time = between(3, 8)
    weight = 5
    _queries = [
        "BTC long-term outlook",
        "Fed rate cut expectations",
        "AI sector rotation",
        "China stimulus impact",
        "ETH staking yield",
    ]

    @task
    def semantic(self):
        q = random.choice(self._queries)
        _req(self.client, "POST", "/api/v1/knowledge/search",
             name="POST /knowledge/search",
             json={"query": q, "limit": 5})


# ── 写入场景（仅 LOAD_INCLUDE_WRITES=1 时启用） ───────────────────


if _INCLUDE_WRITES:

    class WriteUser(HttpUser):
        """写入场景（默认关闭，开启时占 weight=3）.

        ⚠️ 注意：raw-sources POST 会触发 ingestion pipeline，
        其内部调用 LLM。开启前确保后端有 LLM mock 或限流保护，
        否则会快速烧光 API quota。
        """

        wait_time = between(5, 10)
        weight = 3

        @task
        def post_observation(self):
            _req(self.client, "POST", "/api/v1/observations/",
                 name="POST /observations",
                 json={
                     "kind": "note",
                     "summary": f"load test obs {random.randint(1, 9999)}",
                     "content": "synthetic load test content",
                 })

    # ── LLM 写入场景（chat / briefing）— 仅在 LOAD_LLM_MOCK=1 时启用 ─────
    # 这两条路径都重度调用 LLM，未开 OPENROUTER_MOCK=1 会瞬间烧光 API quota。
    # locust 端用单独的 LOAD_LLM_MOCK 开关二次确认（即 后端已设 OPENROUTER_MOCK=1
    # 且压测者明确想跑这两条路径），避免误触发。
    _LOAD_LLM_MOCK = _bool_env("LOAD_LLM_MOCK", False)

    if _LOAD_LLM_MOCK:

        class ChatUser(HttpUser):
            """Chat 场景：覆盖 intent → query_rewrite → semantic_search → fusion 全链路.

            ⚠️ 后端必须设 OPENROUTER_MOCK=1，否则会真实调用 OpenRouter。
            压测重点：连接池 / DB session / chat_harness 的 token 预算 /
                      pgvector 查询 / 结构化响应解析路径。
            """

            wait_time = between(8, 15)
            weight = 2
            _session_id: str | None = None

            _msgs = [
                "BTC 怎么看",
                "ETH 最近基本面",
                "市场情绪如何",
                "BTC 和 ETH 的对比",
                "短线交易建议",
            ]

            def on_start(self):
                # bootstrap 一个 session 复用整个 user 生命周期
                with self.client.post(
                    "/api/v1/chat/sessions",
                    name="POST /chat/sessions",
                    headers=_headers(),
                    json={"title": "load-test", "mode": "chat"},
                    catch_response=True,
                ) as r:
                    if r.status_code in (200, 201):
                        try:
                            self._session_id = r.json().get("id")
                            r.success()
                        except Exception:
                            r.failure(f"Could not parse session id: {r.text[:100]}")
                    else:
                        r.failure(f"Session create failed: HTTP {r.status_code}")

            @task
            def chat_message(self):
                if not self._session_id:
                    return
                msg = random.choice(self._msgs)
                _req(self.client, "POST",
                     f"/api/v1/chat/sessions/{self._session_id}/chat",
                     name="POST /chat/sessions/:id/chat",
                     json={"message": msg, "history": []})

        class BriefingUser(HttpUser):
            """Briefing 场景：覆盖 intent → fetch (MCPs) → fusion → briefing 全链路.

            ⚠️ 同 ChatUser，需要后端 OPENROUTER_MOCK=1。
            压测重点：长链路（Plan-Fetch-Deep）下的并发健康度 + cost_scope
                     累计 + job_run 写入。
            """

            wait_time = between(15, 30)
            weight = 1  # 比 chat 更重，更低 weight

            @task
            def generate_briefing(self):
                _req(self.client, "POST",
                     "/api/v1/briefings",
                     name="POST /briefings",
                     json={
                         "asset_scope": [random.choice(["BTC", "ETH"])],
                         "question": f"load test briefing {random.randint(1, 999)}",
                         "depth": "shallow",
                     })


# ── 退出钩子：失败率超阈值 → exit code 1 ──────────────────────────


@events.quitting.add_listener
def _check_fail_rate(environment: Environment, **kwargs):
    stats = environment.stats.total
    if stats.num_requests == 0:
        environment.process_exit_code = 0
        return
    fail_rate = stats.num_failures / stats.num_requests
    if fail_rate > _FAIL_RATE_THRESHOLD:
        print(
            f"\n[Locust] FAIL rate {fail_rate:.2%} > {_FAIL_RATE_THRESHOLD:.0%} "
            f"({stats.num_failures}/{stats.num_requests})"
        )
        environment.process_exit_code = 1
    else:
        environment.process_exit_code = 0
