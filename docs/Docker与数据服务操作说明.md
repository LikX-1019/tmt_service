# 智能客服 Docker 与数据服务操作说明

更新日期：2026-09-18（Asia/Shanghai）

## 服务与数据职责

当前 Compose 包含 MySQL、商品 PostgreSQL、商品管理服务、etcd、MinIO、Milvus 和 Attu。
FastAPI 主应用在宿主机运行。

| 存储 | 内容 | Schema / 初始化方式 |
| --- | --- | --- |
| MySQL | `chat_conversation_products`、`cs_qa` 及客服业务表 | `uv run alembic upgrade head` |
| PostgreSQL | 商品主数据、`product_api_profiles` | fresh init SQL；已有库运行正式升级脚本 |
| Milvus | QA vectors | QA 导入 / 索引流程 |

不要把 `chat_conversation_products` 描述为 PostgreSQL 表。QA 允许为空；空库返回固定 fallback，
不会调用 LLM 编造。

## 推荐入口

所有 Compose 命令优先通过 worktree helper：

```sh
uv run python scripts/dev_compose.py config --quiet
uv run python scripts/dev_compose.py up -d
uv run python scripts/dev_compose.py ps
uv run python scripts/dev_compose.py info
uv run python scripts/dev_compose.py logs --tail 100
```

该入口自动生成 worktree-scoped project name。Compose 不再固定容器和网络名称；默认 bind
数据目录也是当前 worktree 的 `data/docker`。完整隔离和端口覆盖说明见
[`数据库升级与多Worktree开发.md`](数据库升级与多Worktree开发.md)。

## 首次准备与升级

```sh
cp .env.example .env
uv sync --frozen
uv run python scripts/dev_compose.py up -d mysql product-postgres etcd minio milvus commodity-management
uv run alembic upgrade head
uv run python scripts/upgrade_product_postgres.py
uv run python scripts/upgrade_product_postgres.py --check
```

`.env` 只能保存在本机，不能提交。PostgreSQL 升级脚本不会输出密码或完整 DSN。

## 日常操作

```sh
# 停止但保留容器和数据
uv run python scripts/dev_compose.py stop

# 再次启动
uv run python scripts/dev_compose.py start

# 删除当前 project 的容器和网络，保留 bind 数据目录
uv run python scripts/dev_compose.py down
```

禁止执行 `down -v`、`docker volume prune`、`docker system prune --volumes`，也不要删除
`data/docker`。修改 `.env` 后使用 `up -d --force-recreate` 让容器重新读取配置。

## 服务间与宿主机连接

容器间连接使用 service name：

- MySQL：`mysql:3306`
- 商品 PostgreSQL：`product-postgres:5432`
- Milvus：`milvus:19530`
- MinIO：`minio:9000`
- etcd：`etcd:2379`

宿主机应用使用 `127.0.0.1` 和 `.env` 中对应端口。MySQL、商品 PostgreSQL、Milvus 的
Compose 发布端口分别由 `MYSQL_HOST_PORT`、`PRODUCT_POSTGRES_HOST_PORT`、
`MILVUS_HOST_PORT` 控制；宿主机客户端端口需同步设置为 `MYSQL_PORT`、
`PRODUCT_POSTGRES_PORT`、`MILVUS_PORT`。

## 数据挂载检查

```sh
uv run python scripts/dev_compose.py info
docker inspect "$(uv run python scripts/dev_compose.py ps -q mysql)" --format '{{json .Mounts}}'
docker inspect "$(uv run python scripts/dev_compose.py ps -q product-postgres)" --format '{{json .Mounts}}'
```

检查 `Source` 属于预期 worktree 或显式 `COMPOSE_DATA_DIR` 后，才能执行升级、备份或清理。

## 备份

```sh
mkdir -p data/backup
uv run python scripts/dev_compose.py exec -T mysql sh -lc \
  'mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" --single-transaction "$MYSQL_DATABASE"' \
  > data/backup/customer_service.sql

uv run python scripts/dev_compose.py exec -T product-postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > data/backup/product_catalog.sql
```

恢复属于覆盖性操作，不在日常启动或 schema upgrade 中自动执行。

## QA 清理与重建

QA 关系数据位于 MySQL `cs_qa`，向量副本位于 Milvus。清理或重新导入前阅读
[`QA清空与重建.md`](QA清空与重建.md)，确认目标 host、port、database 和 collection。
商品 PostgreSQL 不属于 QA 清理范围。
