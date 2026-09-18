# macOS Docker 存储配置

本说明对应当前 `docker-compose.yml`（MySQL、商品 PostgreSQL、商品管理服务、etcd、
MinIO、Milvus、Attu）。

## 镜像目录

目标目录：`/Users/li001/Desktop/tmt/develop/docker-01`。

在 Docker Desktop 中进入 **Settings → Resources → Advanced → Disk image location**，点击 **Browse** 选择目标目录，再点击 **Apply**（部分版本显示 Apply & Restart）。由 Docker Desktop 完成磁盘迁移和重启；不要直接移动正在使用的 Docker.raw 文件。

这是 Docker Desktop 全局设置，会影响该 Docker Desktop 管理的所有镜像、容器可写层、构建缓存和命名卷。Mac 上镜像保存在虚拟磁盘文件中，不会按镜像名称分别显示为文件夹。Compose 无法配置这个宿主机存储位置。

官方说明：https://docs.docker.com/desktop/troubleshoot-and-support/faqs/macfaqs/

## 项目数据

Compose 使用绑定挂载，默认数据根目录为当前 worktree 的 `data/docker/`。可通过
`COMPOSE_DATA_DIR` 为 smoke 或同一 checkout 下的第二套环境指定其他目录：

| 服务 | 项目内目录 | 容器内目录 |
| --- | --- | --- |
| MySQL | `data/docker/mysql` | `/var/lib/mysql` |
| 商品 PostgreSQL | `data/docker/product-postgres` | `/var/lib/postgresql/data` |
| etcd | `data/docker/etcd` | `/etcd` |
| MinIO | `data/docker/minio` | `/data` |
| Milvus | `data/docker/milvus` | `/var/lib/milvus` |

Attu 和商品管理服务无持久化数据挂载。上述目录已被 `.gitignore` 排除；备份需单独处理，
提交代码不会备份数据库。停止或删除容器不会删除这些绑定目录，请勿直接删除其中的数据。

Compose 不再设置固定 `container_name` 或固定 network name。推荐统一通过
`uv run python scripts/dev_compose.py ...` 操作，它按 worktree 目录生成稳定 project name。
不同 worktree 因此使用不同容器、网络和默认绑定目录。

## 验证与启动

```sh
cd /Users/li001/Desktop/tmt/code/tmt_service
uv run python scripts/dev_compose.py config --quiet
uv run python scripts/dev_compose.py up -d
uv run python scripts/dev_compose.py ps
uv run python scripts/dev_compose.py info
```

启动后可验证实际挂载：

```sh
docker inspect "$(uv run python scripts/dev_compose.py ps -q mysql)" --format '{{json .Mounts}}'
```

其中 `Source` 应为项目下的 `data/docker/mysql`。镜像位置需在 Docker Desktop 设置中确认；`docker info` 中的 `/var/lib/docker` 是虚拟机内部路径，不代表 Mac 上的磁盘文件位置。
