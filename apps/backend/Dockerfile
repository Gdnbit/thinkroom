# syntax=docker/dockerfile:1.7
# ---------- Stage 1: builder ----------
# 编译 Python 依赖到独立 venv，避免 gcc/dev 头文件污染 runtime 镜像
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 编译期依赖：gcc (C 扩展)、libpq-dev (psycopg2)、libffi/libssl (cryptography 等)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# 独立 venv，便于整包 copy 到 runtime
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# ---------- Stage 2: runtime ----------
# 最小 runtime 镜像，仅保留动态链接库 + tini 做 PID 1 + curl 做 healthcheck
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_HOME=/app

# runtime 系统依赖：
#   libpq5       — psycopg2 运行时
#   libmagic1    — python-magic 文件类型识别
#   tesseract    — pytesseract OCR 运行时（含中英文语言包）
#   tini         — 1号进程，正确处理 SIGTERM
#   curl         — HEALTHCHECK
#   ca-certificates — HTTPS 外呼（OpenRouter）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        libmagic1 \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-chi-sim \
        tini \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 拷贝 builder 阶段的 venv
COPY --from=builder /opt/venv /opt/venv

# 非 root 用户 appuser (UID 10001)
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR ${APP_HOME}

# 先建好 uploads/logs 目录并 chown，容器挂卷后仍由 appuser 可写
RUN mkdir -p ${APP_HOME}/uploads ${APP_HOME}/logs \
    && chown -R appuser:appuser ${APP_HOME}

# 拷贝应用代码（dev compose 会 bind mount 覆盖此目录 → 仍可热重载）
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# Docker 原生健康检查：命中 FastAPI /health（见 main.py）
# 启动最多留 30s 冷启动时间，之后每 30s 一次
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# tini 作 PID 1，优雅处理 SIGTERM（uvicorn worker 收到后会 graceful shutdown）
ENTRYPOINT ["/usr/bin/tini", "--"]

# 生产 MVP 默认：1 个 uvicorn worker，接入 proxy header；dev compose 会 override 为 --reload。
# 当前 ingestion 进度的实时源仍是进程内存 tracker。迁移到 Redis/DB 进度总线前，
# 多 worker 会让轮询请求打到不同进程而丢失实时阶段进度。
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
