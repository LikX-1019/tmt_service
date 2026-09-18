# 智能客服 Docker 与数据服务操作说明

更新日期：2026-09-18（Asia/Shanghai）

## 1. 当前运行与 QA 入库状态

本项目的 `docker-compose.yml` 当前启动 6 个基础设施服务：MySQL、Redis、etcd、MinIO、Milvus 和 Attu。应用服务尚未加入 Compose，`Dockerfile`、`main.py` 及部分业务模块仍是占位文件，因此本文只描述现有基础设施与 QA 数据操作。

2026-09-18 起，旧 JAFFICK QA 源数据已失效并从仓库移除；运行时旧记录位于 MySQL `cs_qa`，向量副本位于 Milvus。请先阅读 `docs/QA清空与重建.md`，由管理员确认环境后执行安全清理脚本。QA 能力、表结构、导入、索引与检索接口全部保留；空知识库会返回固定 fallback，不会调用 LLM 编造。

## 2. 服务地址

以下端口来自当前 `.env`。密码不要写入命令、文档、截图或提交到版本库；需要时从本机 `.env` 读取。

| 服务 | 本机地址 | 账号 / 数据库 | 用途 |
| --- | --- | --- | --- |
| MySQL | `127.0.0.1:3307` | 用户 `tmt_service`，库 `tmt_service` | 标准 QA 关系数据 |
| Redis | `127.0.0.1:6379` | 密码见 `REDIS_PASSWORD` | 会话缓存、短期状态 |
| Milvus | `127.0.0.1:19530` | 当前未配置鉴权 | 向量数据库 |
| Milvus 健康/指标 | `http://127.0.0.1:9091` | 无 | 健康检查和指标 |
| Attu | `http://127.0.0.1:3000` | 连接 `milvus:19530`（容器内） | Milvus Web 管理界面 |
| MinIO API | `http://127.0.0.1:9000` | 账号见 `.env` | Milvus 对象存储依赖 |
| MinIO Console | `http://127.0.0.1:9001` | 账号见 `.env` | MinIO Web 管理界面 |
| etcd | 仅 Compose 内网 `etcd:2379` | 无 | Milvus 元数据依赖 |

容器之间连接时不要使用 `127.0.0.1`，应使用 Compose 服务名，例如 `mysql:3306`、`redis:6379`、`milvus:19530`、`minio:9000` 和 `etcd:2379`。

## 3. 首次准备

1. 安装并启动 Docker Desktop，等待界面显示 Docker Engine 正常运行。
2. 在 PowerShell 进入项目目录：

   ```powershell
   Set-Location 'E:\Desktop\TMT\智能客服'
   ```

3. 检查 Docker 和 Compose：

   ```powershell
   docker version
   docker compose version
   ```

4. 如果没有 `.env`，从模板创建后填写真实密码：

   ```powershell
   Copy-Item '.env.example' '.env'
   ```

`.env` 是本机私密配置，不能提交到 Git。项目应只提交 `.env.example` 中的变量名和占位符。

## 4. 启动、停止和重启 Docker 服务

所有命令都在项目根目录执行。

### 启动

```powershell
docker compose up -d
docker compose ps
```

首次启动需要下载镜像，耗时会比后续启动更长。正常情况下，MySQL、Redis、etcd、MinIO、Milvus 会显示 `healthy`，Attu 显示 `Up`。

### 重启

```powershell
docker compose restart
docker compose ps
```

只重启单个服务：

```powershell
docker compose restart mysql
docker compose restart redis
docker compose restart milvus
```

### 停止和再次启动

停止但保留容器：

```powershell
docker compose stop
```

再次启动已有容器：

```powershell
docker compose start
```

停止并删除容器和网络，但保留本项目绑定到 `data/docker` 的数据：

```powershell
docker compose down
```

不要随意执行 `docker compose down -v`，也不要删除 `data/docker`。这些操作可能导致持久化数据丢失。

### 查看状态和日志

```powershell
docker compose ps
docker compose logs --tail 100
docker compose logs -f mysql
docker compose logs -f redis
docker compose logs -f milvus
```

按 `Ctrl+C` 退出实时日志，不会停止容器。

## 5. 初始化和导入标准 QA

### 初始化数据库和表

首次环境或 `cs_qa` 表不存在时执行：

```powershell
.\scripts\init_qa_db.ps1
```

该脚本创建 `.env` 中 `MYSQL_DATABASE` 指定的数据库和 `cs_qa` 表。重复执行是安全的，不会删除已有数据。

### 导入新的已审核 QA

旧数据清理后，提供新的审核工作簿路径：

```powershell
.\scripts\import_cleaned_qa.ps1 -Source "data\qa\new-reviewed-qa.xlsx"
```

导入脚本会先校验工作表名称、必填列、问题编号、标准问法、标准回答、枚举值和重复编号。数据库以 `qa_code` 为唯一键，重复运行会更新已有记录，不会生成重复 QA。

指定其他工作簿时，路径相对于项目根目录：

```powershell
.\scripts\import_cleaned_qa.ps1 -Source 'data\qa\new-reviewed-qa.xlsx'
```

### 导入后核验

进入 MySQL 后执行：

```sql
SELECT COUNT(*) AS total,
       COUNT(DISTINCT qa_code) AS distinct_codes,
       SUM(status = 'published') AS published,
       SUM(status = 'draft') AS draft,
       SUM(review_status = 'usable') AS usable,
       SUM(review_status = 'pending_review') AS pending_review,
       SUM(review_status = 'pending_validation') AS pending_validation
FROM cs_qa;

SELECT import_batch_no, COUNT(*) AS rows_count, MAX(updated_at) AS updated_at
FROM cs_qa
GROUP BY import_batch_no
ORDER BY updated_at DESC;

SELECT qa_code, COUNT(*) AS rows_count
FROM cs_qa
GROUP BY qa_code
HAVING COUNT(*) > 1;
```

## 6. 连接 MySQL

### 从本机命令行连接

如果本机安装了 MySQL 客户端：

```powershell
mysql -h 127.0.0.1 -P 3307 -u tmt_service -p tmt_service
```

出现 `Enter password` 时输入 `.env` 中的 `MYSQL_PASSWORD`。不要把密码直接写在命令行中。

### 直接进入容器中的 MySQL

无需在 Windows 安装 MySQL 客户端：

```powershell
docker compose exec mysql sh -lc 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"'
```

进入后常用命令：

```sql
SHOW TABLES;
DESCRIBE cs_qa;
SELECT COUNT(*) FROM cs_qa;
SELECT qa_code, standard_question, status FROM cs_qa LIMIT 10;
EXIT;
```

### 使用 Navicat、DBeaver 或 DataGrip

填写以下连接项：

- Host：`127.0.0.1`
- Port：`3307`
- Database：`tmt_service`
- User：`tmt_service`
- Password：`.env` 中的 `MYSQL_PASSWORD`
- Character set：`utf8mb4`
- SSL：本地开发可先关闭；生产环境按实际证书配置

如果客户端也运行在同一个 Compose 网络中，则 Host 改为 `mysql`、Port 改为 `3306`。

## 7. 连接 Redis

### 容器内连接和检查

推荐使用下面的命令，密码只在容器内从环境变量读取：

```powershell
docker compose exec redis sh -lc 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning'
```

进入后常用命令：

```text
PING
DBSIZE
INFO keyspace
SCAN 0 COUNT 100
QUIT
```

不要在生产或数据量较大的环境使用 `KEYS *`，应使用 `SCAN` 分批遍历。

一条命令检查 Redis：

```powershell
docker compose exec -T redis sh -lc 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning PING'
```

### 使用 RedisInsight

- Host：`127.0.0.1`
- Port：`6379`
- Username：留空（当前未配置 ACL 用户）
- Password：`.env` 中的 `REDIS_PASSWORD`
- TLS：关闭（当前本地 Compose 未配置 TLS）

如果客户端运行在 Compose 网络中，则 Host 使用 `redis`，Port 使用 `6379`。

## 8. 连接 Milvus、Attu 和 MinIO

### Milvus

应用或 SDK 从宿主机连接：

```text
URI: http://127.0.0.1:19530
Database: default
```

健康检查：

```powershell
Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:9091/healthz'
```

列出默认数据库中的集合：

```powershell
$body = '{"dbName":"default"}'
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' `
  -ContentType 'application/json' `
  -Body $body
```

### Attu

浏览器打开 `http://127.0.0.1:3000`。如果页面要求填写 Milvus 地址：

- Attu 在当前 Compose 中运行：使用 `milvus:19530`
- 独立运行在 Windows 本机：使用 `127.0.0.1:19530`

### MinIO

浏览器打开 `http://127.0.0.1:9001`，使用 `.env` 中的 `MINIO_ROOT_USER` 和 `MINIO_ROOT_PASSWORD` 登录。API 地址是 `http://127.0.0.1:9000`。

MinIO 和 etcd 是 Milvus 的依赖。排查 Milvus 启动失败时，先确认这两个服务健康：

```powershell
docker compose ps etcd minio milvus
docker compose logs --tail 100 etcd minio milvus
```

## 9. 数据持久化与备份

当前 Compose 使用项目目录绑定挂载：

- MySQL：`data/docker/mysql`
- Redis：`data/docker/redis`
- etcd：`data/docker/etcd`
- MinIO：`data/docker/minio`
- Milvus：`data/docker/milvus`

删除容器通常不会删除这些目录，但直接删除目录会丢失数据。备份前应保证数据一致性，优先使用服务自带导出工具。

MySQL 逻辑备份示例：

```powershell
New-Item -ItemType Directory -Force '.\data\backup' | Out-Null
docker compose exec -T mysql sh -lc 'mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" --single-transaction "$MYSQL_DATABASE"' `
  | Set-Content -Encoding UTF8 '.\data\backup\tmt_service.sql'
```

恢复属于覆盖性操作，执行前应确认目标数据库和备份文件，且先保留当前数据库的备份。

## 10. 常见问题

### 端口被占用

查看占用端口的进程：

```powershell
Get-NetTCPConnection -LocalPort 3307,6379,9000,9001,19530,9091,3000 -ErrorAction SilentlyContinue
```

修改 `.env` 中对应宿主机端口后，重新创建容器：

```powershell
docker compose up -d --force-recreate
```

### MySQL 或 Redis 连接被拒绝

```powershell
docker compose ps
docker compose logs --tail 100 mysql
docker compose logs --tail 100 redis
```

确认连接宿主机时使用 `127.0.0.1:3307` 和 `127.0.0.1:6379`；容器之间使用 `mysql:3306` 和 `redis:6379`。

### 修改 `.env` 后没有生效

`docker compose restart` 不会重新创建容器，也不会把新的环境变量注入已有容器。修改 `.env` 后应执行：

```powershell
docker compose up -d --force-recreate
```

### Milvus 启动较慢

Milvus 依赖 etcd 和 MinIO，首次启动或重启后需要等待健康检查。用以下命令观察：

```powershell
docker compose ps
docker compose logs -f milvus
```

### QA 数量与 Excel 不一致

先重新运行导入脚本。脚本会在写库前报告缺列、空必填值、不支持的枚举或重复问题编号：

```powershell
.\scripts\import_cleaned_qa.ps1
```

如果脚本成功但数量仍不一致，再用第 5 节 SQL 检查批次、唯一编号和状态分布。
