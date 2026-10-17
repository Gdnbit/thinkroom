FROM postgres:16

# 安装 pgvector 扩展
RUN apt-get update && apt-get install -y \
    build-essential \
    postgresql-server-dev-16 \
    git \
    && git clone --branch v0.7.0 https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make \
    && make install \
    && rm -rf /tmp/pgvector \
    && apt-get remove -y build-essential postgresql-server-dev-16 git \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
