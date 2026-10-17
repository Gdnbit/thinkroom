-- PostgreSQL 初始化脚本
-- docker-compose.prod.yml 用 pgvector/pgvector:pg16 镜像，vector 扩展预装
-- 容器首次启动时本脚本被挂载到 /docker-entrypoint-initdb.d 自动执行

-- pgvector：Tier ① 向量搜索（semantic_search.py）
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm：全文模糊搜索（LIKE '%x%' 能走索引）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- uuid-ossp：uuid_generate_v4() 等函数（部分模型需要）
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
