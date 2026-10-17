# 私承 Sicheng

**承接私域思想，让不同思维共同推演。**

[English](README_EN.md) · [文档](docs/README.md) · [贡献](CONTRIBUTING.md)

将私域资料中的观点、思维方式与判断框架转化为思考角色，围绕你的问题展开推演、
质疑与辩证。这是项目的核心方向；角色对资料中思想的还原程度仍需人工验证。

当前支持上传资料、基于选定资料问答或生成简报，以及保存判断、记录结果与回放比较。
资料引用为推演提供可追溯的支撑。技术栈为 React/Vite、FastAPI 和 SQLite。

当前目标是本地单用户工作流。市场、社媒和回测模块含实验性能力；委员会模式尚未
证明质量增益。模型回答需要人工复核，测试通过不代表生产或金融适用性认证。

[桌面预览](examples/ui-desktop.png) · [手机预览](examples/ui-mobile.png)

## 本地启动

需要 Python 3.11+、Node.js 20+。仓库根目录执行：

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
