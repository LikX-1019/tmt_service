# macOS 本地启动说明

项目在 macOS 上采用两部分运行：MySQL、商品 PostgreSQL、商品管理服务、etcd、MinIO、
Milvus 和 Attu 由 Docker Compose 管理；FastAPI 应用使用项目根目录的 `.venv` 在宿主机运行。

## 首次准备

进入项目目录：

```sh
cd /Users/li001/Desktop/tmt/code/tmt_service
```

根据锁文件创建或补齐 Python 环境：

```sh
uv sync --frozen
```

检查 Compose 配置并拉取固定版本镜像：

```sh
uv run python scripts/dev_compose.py config --quiet
uv run python scripts/dev_compose.py pull
```

项目数据保存在 `data/docker/`，Docker 镃像磁盘保存在
`/Users/li001/Desktop/tmt/develop/docker-01/DockerDesktop/Docker.raw`。

## 日常启动

先启动全部基础服务，并等待健康检查完成：

```sh
uv run python scripts/dev_compose.py up -d
uv run python scripts/dev_compose.py ps
```

首次使用或数据库迁移文件有变化时执行：

```sh
uv run alembic upgrade head
uv run python scripts/upgrade_product_postgres.py
uv run python scripts/upgrade_product_postgres.py --check
```

然后启动 FastAPI：

```sh
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

开发时需要代码自动重载，可在命令末尾添加 `--reload`。应用启动后访问：

- 客服控制台：<http://127.0.0.1:8000/>
- 健康检查：<http://127.0.0.1:8000/health>
- Attu：<http://127.0.0.1:3000/>
- MinIO Console：<http://127.0.0.1:9001/>

## 专用 Chrome 配置

在 `.env` 中使用 Google Chrome 的实际可执行文件路径：

```dotenv
PDD_CHROME_EXECUTABLE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PDD_CHROME_PROFILE_DIR=./data/runtime/pdd-chrome
```

修改 `.env` 后必须重启 FastAPI，配置才会重新加载。专用浏览器会使用
`data/runtime/pdd-chrome` 保存独立登录状态，不会复用日常 Chrome 的个人资料。

## 状态与日志

查看容器状态：

```sh
uv run python scripts/dev_compose.py ps
uv run python scripts/dev_compose.py info
```

查看基础服务日志：

```sh
uv run python scripts/dev_compose.py logs --tail 100
```

FastAPI 日志同时写入项目的 `logs/` 目录。应用健康检查返回
`{"status":"ok"}` 表示 Web 进程已正常响应。

## 停止与再次启动

在运行 FastAPI 的终端按 `Ctrl+C` 停止应用。停止基础服务但保留容器和数据：

```sh
uv run python scripts/dev_compose.py stop
```

下次可使用 `uv run python scripts/dev_compose.py start` 再次启动已有容器。若要删除容器和网络但保留
`data/docker/` 中的数据，可执行：

```sh
uv run python scripts/dev_compose.py down
```

不要执行 `down -v`，也不要删除 `data/docker/`；其中保存 MySQL、商品 PostgreSQL、
etcd、MinIO 和 Milvus 的持久化数据。多 worktree 与端口配置见
[`数据库升级与多Worktree开发.md`](数据库升级与多Worktree开发.md)。
