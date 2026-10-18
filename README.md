# ThinkRoom 思考间

**让静态资料中的思想，参与新的思考。**

Bring different minds into the same conversation.

[English](README_EN.md) · [文档](docs/README.md) · [贡献](CONTRIBUTING.md)

ThinkRoom 是一个让资料中的观点、思维方式和不同思想视角参与新问题思考的
多智能体思考工具。它希望承接来源中的思想，让不同角色围绕新问题推演、质疑、
补充并形成新的判断。角色对来源思想的还原程度仍需人工验证。

当前支持上传资料、基于选定资料问答或生成简报，以及保存判断、记录结果与回放比较。
资料引用为推演提供可追溯的支撑。技术栈为 React/Vite、FastAPI 和 SQLite。

当前目标是本地单用户工作流。市场、社媒和回测模块含实验性能力；委员会模式尚未
证明质量增益。模型回答需要人工复核，测试通过不代表生产或金融适用性认证。

## 为什么做 ThinkRoom

传统知识库通常围绕“问题 → 检索资料 → 总结答案”。ThinkRoom 的方向是：

资料 → 提取观点与来源画像 → 让不同思考视角参与新问题 → 讨论、质疑与补充 →
综合判断 → 保存判断并在后续复盘。

当前已实现资料入库、观点卡与来源画像、选定来源问答、简报、带轮次的委员会讨论、
证据追溯、会话历史和判断快照。委员会目前主要使用预设分析席位，并结合检索到的
观点、策略与画像形成上下文；**还不能保证自动把任意作者还原为忠实、独立的 Mind**。
完整的来源思想角色化是演进方向，不是本版已经验证的能力。

## 架构

| 层 | 当前实现 |
|---|---|
| 前端 | React、TypeScript、Vite；问答、资料、复盘及实验性扩展页面 |
| 后端 | FastAPI；上传、解析、入库工作流、来源范围与证据质量约束 |
| 数据库 | 本地 SQLite；Alembic 管理 schema，可选 PostgreSQL/pgvector |
| 检索 | 观点与证据片段、语义检索、重排；SQLite/pgvector 按配置使用 |
| 模型 | OpenRouter 与按角色配置的模型、嵌入和 OCR 服务；支持 Mock |
| 思考工作流 | 来源画像与观点 → 上下文检索 → 动态选择预设席位 → 多轮讨论 → 综合与留痕 |

Redis、Celery 和 MinIO 是部署选项，不是本地 SQLite 体验的必需组件。

## 本地启动

本轮验证使用 Python 3.11、Node.js 22.20；前端要求 Node.js 20.19+。
先选择 Python 3.11 解释器，再在仓库根目录执行：

前端使用现代浏览器（Chrome 111+、Safari 16.4+、Firefox 128+）。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r apps/backend/requirements-dev.txt
.venv/Scripts/python.exe scripts/start_backend.py --mock
```

macOS/Linux 使用 `.venv/bin/python`。启动器迁移独立数据库并在前台运行，Ctrl+C
停止；不会读取秘密库、停止已有服务或导入旧资料。默认数据位于
`.runtime/data/public/local.db`。Mock 只供离线体验，不能用于评价真实模型质量。

另一终端启动前端：

```powershell
cd apps/frontend
npm ci
npm run dev -- --host 127.0.0.1
```

访问 `http://127.0.0.1:5173/chat`；健康检查 `http://127.0.0.1:8000/health`。
可上传 `examples/demo-evidence.txt`。真实模型需用自己的凭据管理器只向后端进程
注入 `OPENROUTER_API_KEY`，再去掉 `--mock`；真实调用会产生费用。
不要将密钥放进源码、截图、Issue 或命令行字面量。配置模板只用于非秘密配置。
旧 `start_backend_sops.ps1` 仅用于兼容旧环境，不是通用入口。

## 模型与配置

默认通过 OpenRouter 调用模型。向启动进程注入 `OPENROUTER_API_KEY`，按需要覆盖
`OPENROUTER_BASE_URL`、`EXTRACTION_MODEL`、`SPEAKER_MODEL`、`BRIEFING_MODEL`、
`COMMITTEE_MODEL_A` / `B` / `C`、`EMBEDDING_MODEL`。其他席位及配置见
`apps/backend/.env.example` 与 `apps/backend/app/config.py`。模型可用性和费用由提供方决定。

后端也会读取被 Git 忽略的 `.env`；本地快捷启动器要求真实 Key 来自调用进程环境。
不要把 `.env` 或实际 Key 纳入版本管理。`--mock` 验证基础链路，不代表真实模型质量，
也不保证所有可选数据源离线。实际模型调用可能将所选资料发送给外部提供方。

## 验证

```powershell
cd apps/backend
../../.venv/Scripts/python.exe -m pytest -q -m "not online and not pg"
cd ../frontend
npm run type-check
npm run lint
npm run test:unit
npm run build
```

根目录运行 `python scripts/check_public_source.py` 检查公开文件完整性。
在线模型、PostgreSQL 和浏览器验收需要单独明确执行。

## 安全

仅导入你有权使用的资料，确认其允许发送给配置的模型服务。开发服务保持 loopback，
不要直接暴露到公网。生产需配置独立强凭据、API 认证、TLS 和受控存储；开发示例密码
不可复用。本版为本地单用户工作流，没有完整多租户隔离。详见 [安全策略](SECURITY.md)。

## 数据边界

上传 → RawSource/ClaimCard/EvidenceSpan → 选定资料问答/简报 → DecisionSnapshot →
Outcome → Replay。证据不足应停止生成，质量警告应保留。PDF 支持文本层和按页
OCR；OCR 取决于本地 Tesseract 或所配置的模型服务，质量随资料变化。

源码不携带作者数据库、研究资料、运行报告、个人工作日志或旧提交历史。
运行报告、上传、日志和备份应保持忽略。开发 Compose 只绑定 loopback，示例
密码只供本地开发；生产配置、多用户隔离需要独立验证。

## 许可

自有代码使用 [Apache-2.0](LICENSE)。外部依赖保留各自许可，详见
[第三方说明](THIRD_PARTY_NOTICES.md)。PDF 实现采用 PDFium，不要求 PyMuPDF。
准备范围与验证见 [PUBLIC_RELEASE.md](docs/PUBLIC_RELEASE.md)。
