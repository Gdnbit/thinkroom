# ThinkRoom · 合成资料演示 / Synthetic demo

[返回中文首页](../README.md) · [English home](../README_EN.md)

**这是可自行运行的演示步骤，不是预录视频、产品截图或已经验证的模型输出。**

This is a reproducible walkthrough, **not a recording, screenshot, or claimed live-model result**.

## 1. 示例场景 / Scenario

仓库的 [\`examples/demo-evidence.txt\`](../examples/demo-evidence.txt) 只包含一个完全虚构的社区图书馆案例：

| 已知信息 / Known | 未知信息 / Unknown |
| --- | --- |
| 第一周 80 人次，第二周 96 人次（增加 20%） | 人员成本 / Staffing costs |
| 试点有一位志愿者 / One volunteer | 长期需求 / Long-term demand |
| 资料建议继续有限试点并采集成本 | 志愿者后续能否持续参与 |

**决策问题：** 是否应该继续延长晚间开放时间？在扩大试点前，需要先验证什么？

**Decision question:** Should extended evening hours continue, and what evidence is missing before expansion?

示例中「增长 20%」只是由 80 与 96 算出的算术结果。它**不代表**试点已盈利，也不能证明长期增长。

## 2. 启动本地体验 / Start locally

环境要求、Windows/macOS/Linux 完整命令见[首页 Quick Start](../README.md#现在就试一试本地-mock-模式)。

1. 在仓库根目录启动隔离后端：\`python scripts/start_backend.py --mock\`（使用你已创建的 Python 3.11 虚拟环境）。
2. 在另一个终端进入 \`apps/frontend\`，执行 \`npm ci\` 与 \`npm run dev -- --host 127.0.0.1\`。
3. 打开 http://127.0.0.1:5173/chat，进入问答页面。
4. 上传 \`examples/demo-evidence.txt\`，等待入库处理完成；如页面提供资料范围选择，请将问题限定到这份资料。
5. 尝试下面的问题，观察上传、问答、引用、质量提示以及当前 UI 支持的其他模式。

**Mock 提醒：** Mock 只用于验证技术链路，可能不会产生贴合此案例的自然语言推理结论；不要把占位回答当成产品演示成果。要验证真实模型行为，需要由用户自行配置有效提供方与密钥，可能产生费用并向提供方传输所选资料。

## 3. 建议试的问题 / Suggested prompts

**问题 A · 证据支持**

> 根据这份试点资料，支持继续延长开放时间的事实有哪些？哪些信息尚未收集？

What in the trial document supports continuing extended hours, and what is still unknown?

**问题 B · 反对意见**

> 站在成本控制和执行风险的视角，质疑直接扩大试点的做法。每个事实性判断都说明来源依据或证据不足。

Challenge immediate expansion from cost and execution-risk perspectives. Identify supporting evidence or gaps.

**问题 C · 附条件行动**

> 给出一个有条件的下一步方案，并列出在做决定之前必须补充的数据。不要把推测说成事实。

Propose a conditional next step, listing missing evidence without presenting assumptions as facts.

### 观察要点 / What to inspect

- **来源范围：** 系统是否仍然围绕选定文档，而不是引入未经授权的其他数据？
- **引用与质量：** 哪些结论来自文档，哪些属于模型推论，哪些问题没有证据？
- **不同模式：** 普通问答、简报及委员会讨论的输出结构有何区别？委员会模式需要更多时间和成本。
- **判断与复盘：** 如流程支持保存判断，后续可通过复盘页记录结果，比较原判断与后续事实。

这些是**需要检查的行为**，不是对所有模型提供商、每份文档或 Mock 模式的效果保证。

## 4. 一个合理的分析框架，不是模型生成记录

下面只是人根据原始合成资料整理的**示例分析框架**，可帮助你判断生成结果有没有越过证据边界。

| 观察 | 应有的证据边界 |
| --- | --- |
| 到访从 80 增至 96 | 只能说明这两周记录的访问量提高 20% |
| 可以继续探索试点 | 是文件提出的有条件建议，不代表证明长期有效 |
| 成本是否可接受 | 尚未测量，不应声称已得到确认 |
| 长期需求是否存在 | 尚无足够数据，不应直接外推 |
| 志愿者无法继续怎么办 | 原文建议恢复原有时段 |

不要将本表称为「模型实测回答」或「系统自动生成的委员会结论」。

## 5. 展示素材安全 / Safe screenshots

未来添加真实产品截图或视频时：

1. 只使用本合成资料或重新生成的无个人身份信息的测试资料。
2. 截取**实际运行**的 ThinkRoom UI；不要生成虚假产品截图冒充真实界面。
3. 检查窗口标题、浏览器书签、地址栏、会话历史、上传文件名、用户名、模型 Key、日志和嵌入元数据。
4. 逐张检查后再加入仓库并更新公共源码 Manifest。
5. Mock 画面标注「Mock / synthetic demo」；真实模型示例需说明模型、输入和验证边界。

The repository does **not** ship live customer data, private research documents or validated AI-quality screenshots. A screenshot is useful only when it is authentic, sanitized and correctly labeled.

---

需要部署、配置、审计信息？访问[文档导航](README.md)。真实模型请求可能产生费用，所选资料可能被发送到第三方模型服务。
