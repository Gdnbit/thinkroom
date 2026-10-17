# Production Secret Source Provenance Runbook

> **延后范围**：本文仅供未来生产密钥来源核验；本地 v0.1 只使用现有 SOPS-backed
> OpenRouter 启动路径，不据此宣称生产就绪。

> 适用范围:生产发布前，为密钥和凭据型配置补齐来源证明，确保 `validate_production_env.py`、`run_release_gate.py` 和运行时 readiness 报告的判断一致。
> 最近更新:2026-07-10
> 关联脚本:`scripts/validate_production_env.py` · `scripts/validate_secret_inventory.py` · `scripts/run_release_gate.py`
> 关联文档:`docs/operations/DEPLOYMENT_RUNBOOK.md` · `docs/operations/SECURITY_CHECKLIST.md`

---

## 1. 为什么需要来源证明

生产环境只检查“值存在”不够。`DATABASE_URL`、`APP_API_KEY`、`OPENROUTER_API_KEY`、`REDIS_URL`、`MINIO_ACCESS_KEY`、`SENTRY_DSN` 等值可能来自临时手填、用户级环境变量、明文文件或受控密钥系统。系统需要知道这些值是否由 SOPS、Vault、云密钥服务或部署 secret store 注入。

来源证明只记录来源家族，不记录密钥值。报告会显示 `sops`、`vault`、`aws-secrets-manager` 等脱敏来源，不应显示原始 `sops:...` 路径、明文凭据或本机路径。

---

## 2. 推荐配置方式

### 2.1 全局来源:同一受控来源注入全部密钥

当所有生产密钥和凭据型配置都来自同一个受控来源时，配置一个全局标记即可:

```env
SECRETS_SOURCE=sops:production
```

这适用于统一通过 SOPS 文件、部署平台 secret store 或 Vault 统一注入的环境。

### 2.2 逐项来源:不同键来自不同受控来源

当不同配置来自不同系统时，为具体键设置 `*_SOURCE`:

```env
DATABASE_URL_SOURCE=vault:DATABASE_URL
APP_API_KEY_SOURCE=sops:APP_API_KEY
APP_SECRET_KEY_SOURCE=sops:APP_SECRET_KEY
OPENROUTER_API_KEY_SOURCE=sops:OPENROUTER_API_KEY
REDIS_URL_SOURCE=sops:REDIS_URL
RATE_LIMIT_STORAGE_URI_SOURCE=sops:RATE_LIMIT_STORAGE_URI
MINIO_ENDPOINT_SOURCE=sops:MINIO_ENDPOINT
MINIO_BUCKET_SOURCE=sops:MINIO_BUCKET
MINIO_ACCESS_KEY_SOURCE=sops:MINIO_ACCESS_KEY
MINIO_SECRET_KEY_SOURCE=sops:MINIO_SECRET_KEY
LOG_FORMAT_SOURCE=sops:LOG_FORMAT
SENTRY_DSN_SOURCE=sops:SENTRY_DSN
```

如果同时设置全局和逐项来源，逐项来源用于对应键；缺失逐项来源时回退到 `SECRETS_SOURCE`。

### 2.3 可选敏感来源:填值启用时才要求来源

部分外部信息源是可选能力，例如市场数据 API、搜索 API、OCR/视觉 API、社媒 Cookie 或 RSS/token。它们未配置时不阻断生产发布；一旦在候选生产 env 中填入非空值，就必须由 `SECRETS_SOURCE` 覆盖，或补充对应 `*_SOURCE`:

```env
COINGECKO_API_KEY_SOURCE=sops:COINGECKO_API_KEY
FRED_API_KEY_SOURCE=sops:FRED_API_KEY
ALPHA_VANTAGE_API_KEY_SOURCE=sops:ALPHA_VANTAGE_API_KEY
TAVILY_API_KEY_SOURCE=sops:TAVILY_API_KEY
GOOGLE_AI_API_KEY_SOURCE=sops:GOOGLE_AI_API_KEY
TWITTER_COOKIE_SOURCE=1password:TWITTER_COOKIE
WEIBO_COOKIE_SOURCE=1password:WEIBO_COOKIE
XHS_COOKIE_SOURCE=1password:XHS_COOKIE
DOUYIN_COOKIE_SOURCE=1password:DOUYIN_COOKIE
WEMPRSS_API_TOKEN_SOURCE=sops:WEMPRSS_API_TOKEN
```

这样可以保留“可选能力按需启用”的灵活性，同时避免生产环境中出现来源不可追踪的外部 API key 或 Cookie。可选敏感值本身也必须是真实值；候选生产 env 中不要保留 `<REQUIRED_...>`、`placeholder`、`from-secret-store` 等占位符。若该能力暂不启用，应移除对应值，而不是保留占位值。`validate_production_env.py` 和 `validate_secret_inventory.py` 都会执行这条规则，避免旧版或手工生成的 release artifact 绕过生产门禁。

发布报告中的 `alternative_config_keys` 只列出当前候选 env 需要补齐的活跃来源标记。未填值、未启用的可选来源不会出现在 action plan 或 secret inventory 中，避免把未启用能力误判为发布阻断项。

如果删除或停用某个可选来源值，也要同时删除它的 `*_SOURCE`。例如移除 `FRED_API_KEY` 时不要保留 `FRED_API_KEY_SOURCE`。候选生产 env 中出现孤立的可选 `*_SOURCE` 会被视为配置漂移并阻断发布，直到删除来源标记或重新启用对应可选值。

---

## 3. 当前接受的来源家族

生产校验接受以下前缀:

- `sops:`
- `secret-store:`
- `vault:`
- `aws-secrets-manager:`
- `gcp-secret-manager:`
- `azure-key-vault:`
- `kubernetes-secret:`
- `doppler:`
- `1password:`

不接受:

- `plain-env:manual-copy`
- `environment_or_settings`
- `default_or_missing`
- 空值
- 占位符，例如 `<REQUIRED_CONTROLLED_SECRET_SOURCE>`、`from-secret-store`、`placeholder`

---

## 4. 发布前验证命令

先验证 SOPS/部署密钥注入是否足够，但不写出真实 env 文件:

```powershell
$env:SOPS_AGE_KEY_FILE = [Environment]::GetEnvironmentVariable('SOPS_AGE_KEY_FILE', 'User')
sops exec-env "<private-secret-store>" "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error"
```

确认 dry-run 通过后，再显式写出 gitignored 的候选生产 env 文件:

```powershell
sops exec-env "<private-secret-store>" "python scripts/materialize_production_env.py --overwrite --source-label sops:production --fail-on-error"
```

`materialize_production_env.py` 不会在 JSON 报告中输出密钥值，并兼容本机 SOPS 短名 `openrouter_key`、`secrets env` 导出的 `OPENROUTER_KEY`、以及部署环境常用的 `OPENROUTER_API_KEY`。报告只记录脱敏来源类型，例如 `sops_short_name`、`legacy_env_alias`、`standard_env`，避免历史密钥标记进入可归档报告。

如果 dry-run 失败，先查看 `docs/verification/production-env-materialization-latest.json` 中的 `missing_config_requests`。该字段是后续补 SOPS/部署密钥的权威清单，每项包含:

- `config_key`: 生产 env 最终需要的变量名。
- `suggested_sops_key`: 建议写入 `<private-secret-store>` 或部署 secret store 的项目级短名。
- `source_marker_key`: 若不用全局 `SECRETS_SOURCE`，该项对应的来源证明变量。
- `sensitivity`: 是否属于 secret、secret URI、运行凭据或非敏感发布配置。
- `can_be_derived_from`: 可由其他变量派生的关系，例如 `DATABASE_URL` 可由 `POSTGRES_PASSWORD` 派生。
- `accepted_source_prefixes`: 允许的来源家族，例如 `sops:`、`vault:`、`kubernetes-secret:`。

`materialize_production_env.py` 会直接识别 `suggested_sops_key`。例如写入 `adms_app_api_key` 后，脚本会物化为标准生产变量 `APP_API_KEY`；写入 `adms_postgres_password` 后，脚本会物化 `POSTGRES_PASSWORD`，并在未显式提供 `adms_database_url` / `DATABASE_URL` 时派生 `DATABASE_URL`。若通过 `secrets env` 这类工具导出了大写短名，例如 `ADMS_APP_API_KEY`，脚本也会识别并在报告中标记为 `project_env_alias`。

运行中的维护接口也会暴露同一份清单: `GET /api/v1/health/release-readiness` 的 `production_env_materialization` 字段只保留缺失配置名、建议 SOPS key、来源标记、来源前缀和 operator 命令；不会返回 `description`、`policy_basis`、密钥值或本机绝对路径。维护页据此显示“待补生产配置”，用于确认还缺哪些 key，但真实值仍必须通过 SOPS、Vault 或部署 secret store 注入。

验证候选生产 env 文件:

```powershell
python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error
```

验证 release gate:

```powershell
python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod
```

验证 release gate 生成的密钥清单:

```powershell
python scripts/validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --env-file apps/backend/.env.prod --fail-on-error
```

`apps/backend/.env.prod.example` 是占位示例，预期不能通过生产门禁。真正发布时应传入部署环境对应的候选生产 env 文件。

---

## 5. 常见失败与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `SECRETS_SOURCE` failed | 没有全局来源，也没有补齐所有逐项来源 | 设置 `SECRETS_SOURCE=sops:production`，或补齐所有 required `*_SOURCE` |
| `APP_API_KEY_SOURCE` failed | 逐项来源为空、占位、或不受信 | 改为 `sops:`、`vault:` 等受信前缀 |
| `COINGECKO_API_KEY` 等可选敏感值 failed | 可选值已启用但仍是占位符 | 从 SOPS/secret store 注入真实值；若暂不启用则移除该值和对应 `*_SOURCE` |
| action plan 显示 `*_SOURCE` 但 secret inventory 不包含它 | 正常；`*_SOURCE` 是来源元数据，不是密钥值 | 更新部署配置来源标记后重跑 release gate |
| runtime readiness 仍显示来源不可审计 | 运行中的后端未加载新的来源标记 | 用同一 env/secret 注入方式重启后端，再访问 `/api/v1/health/release-readiness` |
| `plain-env:manual-copy` 被拒绝 | 手工复制不具备生产审计链 | 将该值迁移到 SOPS、Vault 或部署 secret store |

---

## 6. 发布判定

生产发布前至少满足:

- `python scripts/validate_production_env.py --env-file <candidate-production-env> --fail-on-error` 通过。
- `python scripts/run_release_gate.py --require-env-file --env-file <candidate-production-env>` 返回 release allowed。
- `/api/v1/health/release-readiness` 中 secrets、shared Redis、object storage、rate limit storage、observability 的生产来源证明均为 pass 或明确的生产例外。
- 生成的 `docs/verification/*latest*` 报告不包含密钥值、原始本机路径或内部 URL。
