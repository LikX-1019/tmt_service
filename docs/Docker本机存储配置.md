# macOS Docker 存储配置

本说明对应当前 `docker-compose.yml`（MySQL、etcd、MinIO、Milvus、Attu 共 5 个服务）。

## 镜像目录

目标目录：`/Users/li001/Desktop/tmt/develop/docker-01`。

在 Docker Desktop 中进入 **Settings → Resources → Advanced → Disk image location**，点击 **Browse** 选择目标目录，再点击 **Apply**（部分版本显示 Apply & Restart）。由 Docker Desktop 完成磁盘迁移和重启；不要直接移动正在使用的 Docker.raw 文件。

这是 Docker Desktop 全局设置，会影响该 Docker Desktop 管理的所有镜像、容器可写层、构建缓存和命名卷。Mac 上镜像保存在虚拟磁盘文件中，不会按镜像名称分别显示为文件夹。Compose 无法配置这个宿主机存储位置。

官方说明：https://docs.docker.com/desktop/troubleshoot-and-support/faqs/macfaqs/

## 项目数据

Compose 已使用绑定挂载，数据保存在 `/Users/li001/Desktop/tmt/code/tmt_service/data/docker/`：

| 服务 | 项目内目录 | 容器内目录 |
| --- | --- | --- |
| MySQL | `data/docker/mysql` | `/var/lib/mysql` |
| etcd | `data/docker/etcd` | `/etcd` |
| MinIO | `data/docker/minio` | `/data` |
| Milvus | `data/docker/milvus` | `/var/lib/milvus` |

Attu 是管理界面，当前无持久化数据挂载。上述目录已被 `.gitignore` 排除；备份需单独处理，提交代码不会备份数据库。停止或删除容器不会删除这些绑定目录，请勿直接删除其中的数据。

## 验证与启动

```sh
cd /Users/li001/Desktop/tmt/code/tmt_service
docker compose config --quiet
docker compose up -d
docker compose ps
```

启动后可验证实际挂载：

```sh
docker inspect "$(docker compose ps -q mysql)" --format '{{json .Mounts}}'
```

其中 `Source` 应为项目下的 `data/docker/mysql`。镜像位置需在 Docker Desktop 设置中确认；`docker info` 中的 `/var/lib/docker` 是虚拟机内部路径，不代表 Mac 上的磁盘文件位置。
