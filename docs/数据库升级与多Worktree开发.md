# 数据库升级与多 Worktree 开发

## 数据职责

三类存储职责必须保持分离：

```text
MySQL
├── chat_conversation_products
├── cs_qa
└── 会话、消息、任务及审计等客服业务表

PostgreSQL
├── products / product_variants
└── product_api_profiles

Milvus
└── QA vectors
```

Alembic 只管理 MySQL。`chat_conversation_products` 不是 PostgreSQL 表。商品 PostgreSQL
使用独立升级命令；Milvus collection 是 MySQL QA 内容的向量副本，不是商品事实源。

## 部署顺序

更新代码后按以下顺序执行，任何命令失败都应停止部署：

```sh
# 1. 启动或确认基础服务
uv run python scripts/dev_compose.py up -d mysql product-postgres etcd minio milvus commodity-management

# 2. 升级 MySQL schema
uv run alembic upgrade head

# 3. 幂等升级已有商品 PostgreSQL
uv run python scripts/upgrade_product_postgres.py

# 4. 只读验证商品 profile view
uv run python scripts/upgrade_product_postgres.py --check

# 5. 启动或重启宿主机 FastAPI
uv run uvicorn main:app --host 127.0.0.1 --port 8000

# 6. Smoke
curl --fail http://127.0.0.1:8000/health
```

QA 可以为空；空库请求应返回固定 fallback。QA 导入属于独立的后续流程。

## 商品 PostgreSQL 的两条升级路径

- 全新数据目录：PostgreSQL entrypoint 依次执行 `init/001...sql` 和 `init/002...sql`。
- 已有数据目录：运行 `scripts/upgrade_product_postgres.py`。

两条路径都执行唯一的 view definition：
`docker/product-postgres/sql/product_api_profiles.sql`。共享 SQL 使用
`CREATE OR REPLACE VIEW`，不会删除商品表或商品数据。

升级脚本从 `.env` 或进程环境读取 `PRODUCT_POSTGRES_HOST`、
`PRODUCT_POSTGRES_PORT`、`PRODUCT_POSTGRES_DB`、`PRODUCT_POSTGRES_USER` 和
`PRODUCT_POSTGRES_PASSWORD`。输出只包含环境、host、port 和 database，不显示密码或完整 DSN。
升级后会查询 PostgreSQL catalog，确认 `product_api_profiles` 存在且 required fields 是实际
字段的子集；允许未来新增字段，缺少 required field 时退出码非零。

## Compose worktree 隔离

`docker-compose.yml` 不再固定 `container_name`、顶层 project name 或 network name。
统一入口为：

```sh
uv run python scripts/dev_compose.py up -d
uv run python scripts/dev_compose.py ps
uv run python scripts/dev_compose.py logs --tail 100
uv run python scripts/dev_compose.py info
uv run python scripts/dev_compose.py down
```

helper 默认从当前 worktree 目录名生成合法、稳定、最长 48 字符的 project name。也可以显式
传入 `--project-name`，或在 shell 中设置 `COMPOSE_PROJECT_NAME`。`info` 只输出 project、
worktree、branch、容器、网络和数据挂载，不输出 Compose 环境变量或 secret。

当前持久化仍使用 bind mounts。默认 `COMPOSE_DATA_DIR=./data/docker`，因此不同 worktree
自然使用不同宿主机目录；同一 worktree 同时运行 smoke 环境时，必须设置独立的绝对
`COMPOSE_DATA_DIR`。不要把两个 project 指向同一个数据目录。

## 并行 worktree 端口

容器间始终使用 service name 和容器端口：`mysql:3306`、`product-postgres:5432`、
`milvus:19530`、`minio:9000`、`etcd:2379`。宿主机发布端口可以覆盖：

```sh
COMPOSE_PROJECT_NAME=tmt-customer-demo-feature \
COMPOSE_DATA_DIR=/absolute/path/to/isolated-data \
MYSQL_HOST_PORT=13306 \
PRODUCT_POSTGRES_HOST_PORT=15432 \
MILVUS_HOST_PORT=29530 \
MINIO_API_PORT=19000 \
MINIO_CONSOLE_PORT=19001 \
MILVUS_METRICS_PORT=19091 \
ATTU_PORT=13000 \
COMMODITY_MANAGEMENT_PORT=18088 \
uv run python scripts/dev_compose.py up -d
```

如果 FastAPI、Alembic 或升级脚本在宿主机运行，还要令 `MYSQL_PORT`、
`PRODUCT_POSTGRES_PORT`、`MILVUS_PORT`、`MINIO_ENDPOINT` 和 `PRODUCT_API_BASE_URL`
对应上述发布端口。

## 现有开发数据

隔离改动不会自动搬迁、复制或删除旧数据。旧容器仍引用其原 bind mount；需要继续使用旧
环境时，应先用 `docker inspect` 记录实际挂载，再以原 project 和目录操作。禁止为了切换
project 执行 `docker compose down -v`、`docker volume prune` 或删除 `data/docker`。

## 隔离 smoke 原则

干净环境必须同时指定独立 project、独立数据目录和不冲突的全部宿主端口。验证至少包括：

- `docker compose config` 成功；
- MySQL Alembic upgrade 成功；
- fresh PostgreSQL init 创建完整 `product_api_profiles`；
- 将 smoke view 模拟为旧结构后，正式 upgrade 恢复 required fields；
- Milvus、商品管理服务和 FastAPI 健康；
- 空 QA 的 `/api/v1/chat` 返回固定 fallback。

smoke 不应连接或复制现有开发数据库。结束后只允许停止该 smoke project；不得操作其他
project 的容器、网络或数据目录。
