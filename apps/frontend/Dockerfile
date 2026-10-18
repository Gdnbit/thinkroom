# syntax=docker/dockerfile:1.7
# ---------- Stage 1: builder ----------
# 用完整 node 镜像打包产物，避免 alpine 缺 native binary 的坑
FROM node:20-alpine AS builder

ENV CI=1 \
    NODE_ENV=production

WORKDIR /build

# 先 copy lockfile 做依赖层缓存
COPY package.json package-lock.json* ./

# 用 npm ci（严格按 lockfile，快 + 可复现）；devDependencies 也要（vite/tsc 在构建期需要）
RUN npm ci --include=dev --no-audit --no-fund --prefer-offline

# 再 copy 源码（源码变了只重跑 build，不重装依赖）
COPY . .

# VITE_API_URL 通过 build arg 注入（默认相对路径 /api，由 nginx 代理到 backend）
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}

# 产物到 /build/dist
RUN npm run build

# ---------- Stage 2: runtime (nginx) ----------
FROM nginx:1.27-alpine AS runtime

# SPA 回退 + /api 反向代理 + gzip + 长缓存（见 nginx.conf）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 静态产物
COPY --from=builder /build/dist /usr/share/nginx/html

# 非 root 用户已在 nginx:alpine 镜像提供（nginx 官方镜像默认 root，
# 但 worker 进程以 nginx 用户跑）；容器 listen 80
EXPOSE 80

# nginx:alpine 自带 /docker-entrypoint.sh，直接用默认 CMD
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/health || exit 1
