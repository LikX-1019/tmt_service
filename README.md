# Customer Service Agent

电商智能客服项目。当前包含拼多多多店铺本地客服中台、每店独立 Chrome 页面连接器、
会话与发送任务持久化、SSE 实时更新，以及 FAQ + Hybrid RAG 知识问答模块。

拼多多接入只操作页面上可见的 DOM，不调用未公开 HTTP 接口，也不解析平台私有
WebSocket 帧。每个店铺使用独立发送队列并验证己方消息气泡；点击后无法确认的任务会进入
`uncertain`，不会自动重试。

QA 模块由 `QAService` 统一编排，不依赖 FastAPI 或 LangGraph。它优先执行 FAQ
精确匹配，未命中时并行执行 Milvus Dense Search 与内存 BM25，使用 RRF 融合、
BGE Reranker 和证据阈值后生成答案；证据不足时直接返回固定 fallback。

当前 Customer Demo 和新聊天集成的主入口是 `POST /api/v1/chat`。后端统一执行：

```text
Customer Demo / Client
        ↓
POST /api/v1/chat
        ↓
ChatService
        ↓
AgentRuntime
        ↓
Unified Chat AgentGraph
        ↓
FAQ / Guard / Social / Product / RAG / Fallback
```

PDD 使用同一套业务决策核心：

```text
BrowserMessage
        ↓
ConsoleRuntime channel adapter
        ↓
AgentRuntime
        ↓
shared AgentGraph
        ↓
AutoReplyPolicy / persistence / outbound
```

G6 已删除 legacy runtime 开关和备用 AI 编排路径。当前 Unified Chat 与 PDD 只有
上述共享 AgentGraph 业务路径；如需回退迁移结果，使用 Git revert，而不是通过
运行时配置切换。

`POST /api/v1/qa` 仍是独立知识问答接口，供 QA/RAG 调试和兼容调用使用；它不是
Customer Demo 的默认主链路。

## 环境要求

- Python 3.11+
- 推荐使用 [uv](https://docs.astral.sh/uv/)

## 安装

项目固定使用 uv 管理的 CPython 3.12.13 和根目录 `.venv`，不复用系统 Python、
Anaconda 或用户级 `site-packages`：

```powershell
uv sync --frozen
```

`dev` 是 uv 的默认 dependency group，因此该命令同时安装测试与静态检查依赖。
生产镜像使用 `uv sync --frozen --no-group dev`，不维护第二套 pip/Poetry 流程。

同步完成后可直接使用专属解释器：

```powershell
.venv\Scripts\python.exe -V
```

运行时直接依赖及当前锁定版本如下；全部传递依赖和哈希以 `uv.lock` 为准：

| 用途 | 库 | 锁定版本 |
| --- | --- | --- |
| MySQL 异步访问 | aiomysql | 0.3.2 |
| MySQL 8 加密认证 | cryptography | 47.0.0 |
| Web API | fastapi | 0.141.1 |
| HTTP 客户端 | httpx | 0.28.1 |
| 中文分词 | jieba | 0.42.1 |
| LLM 编排 | langchain | 1.4.0 |
| OpenAI-compatible LLM | langchain-openai | 1.6.2 |
| Excel QA 导入 | openpyxl | 3.1.5 |
| 数据库迁移 | alembic | 1.20.0 |
| 专用浏览器 | playwright | 1.62.0 |
| 数据模型 | pydantic | 2.13.5 |
| 环境配置 | pydantic-settings | 2.15.0 |
| Milvus 客户端 | pymilvus | 2.6.17 |
| 稀疏检索 | rank-bm25 | 0.2.2 |
| BGE Embedding/Reranker | sentence-transformers | 5.7.0 |
| 数据库抽象 | sqlalchemy[asyncio] | 2.0.52 |
| ASGI 服务 | uvicorn[standard] | 0.53.0 |
| 测试 | pytest、pytest-asyncio | 8.4.2、1.4.0 |
| 静态检查 | ruff | 0.15.22 |

查看当前环境的完整依赖树：

```powershell
uv tree
```

## 配置

复制 `.env.example` 为 `.env`，至少按实际模型服务填写：

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=replace_with_llm_api_key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

`LLM_BASE_URL` 留空时使用 `langchain-openai` 默认地址。DeepSeek、Qwen
及其他服务可通过其 OpenAI-compatible 地址接入。不要提交含真实密钥的
`.env`，程序和日志也不会输出 API Key。

默认启用 `LLM_WARMUP_ON_STARTUP=true`。服务启动后会在后台加载并缓存模型客户端，
不会发起模型请求或产生模型费用；页面和健康检查无需等待预热即可访问。这样可以避免
Windows 首次导入 `langchain-openai` 较慢时阻塞 FastAPI 事件循环。

## 启动

先启动数据服务，再分别升级 MySQL 与商品 PostgreSQL：

```powershell
uv run python scripts/dev_compose.py up -d mysql product-postgres etcd minio milvus commodity-management
uv run alembic upgrade head
uv run python scripts/upgrade_product_postgres.py
uv run python scripts/upgrade_product_postgres.py --check
```

Alembic 只管理 MySQL 客服业务持久化，包括 `shops`、`customers`、`conversations`、
`messages`、`outbound_jobs`、`reply_decisions`、`connector_events`、`cs_qa` 和
`chat_conversation_products`。商品事实位于独立 PostgreSQL，包含 `products`、
`product_variants` 和面向已发布商品客服资料的 `product_api_profiles` 视图；
全新数据库使用 init SQL，已有数据库必须执行 `upgrade_product_postgres.py`。Milvus
只保存 QA/RAG vector index，不保存商品主数据或长期业务状态。
完整部署顺序及多 worktree 隔离方式见
[`docs/数据库升级与多Worktree开发.md`](docs/数据库升级与多Worktree开发.md)。

旧 JAFFICK QA 数据已失效并移除。首次迁移后如需重建 QA，先参考 `docs/QA清空与重建.md` 清理旧环境，再导入新的已审核工作簿：

```powershell
powershell -File scripts/import_cleaned_qa.ps1 -Source "data/qa/new-reviewed-qa.xlsx"
```

导入会校验表头和枚举值。审核可用的数据设置为 `retrieval_enabled=true`；源工作簿没有
明确的无人值守发送授权列，因此 `auto_reply_eligible` 安全默认为 `false`。

```powershell
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

或显式使用项目解释器：

```powershell
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：`GET /health`。

默认 `CONSOLE_ENABLED=true`，会保留原多店控制台行为并恢复需要在线的 PDD
Chrome 窗口。开发或调试独立 QA/RAG 链路时可设置：

```powershell
$env:CONSOLE_ENABLED="false"
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

此时不会实例化 `ShopRuntimeManager`、`ConsoleRuntime`、PDD Connector 或 Chrome；
`/health`、`/api/v1/chat`、`/api/v1/qa`、`/customer-demo` 与 `/qa-demo` 继续可用，Console 相关 API 返回
`503 CONSOLE_UNAVAILABLE`。控制台页面保留，重新设置 `CONSOLE_ENABLED=true` 并重启即可。

客服控制台：启动服务后访问 `http://127.0.0.1:8000/`。页面无需安装 Node.js 或
执行前端构建。Unified Chat 测试页是 `http://127.0.0.1:8000/customer-demo`；原聊天测试页
保留在 `http://127.0.0.1:8000/chat-demo`。

本地服务导航台：`http://127.0.0.1:8000/service-hub`。页面集中入口包含客服控制台、
Customer Demo、QA Demo、旧聊天页、OpenAPI、健康检查和商品资料管理，并每 5 秒探测
主服务 HTTP、商品服务 HTTP 与 Docker Compose 依赖状态。页面支持白名单内的核心依赖
启动 / 重启 / 停止，以及主服务重启和停止；运维 API 仅接受本机请求和专用请求头。
注意：主服务完全停止后，该页面本身也会下线，重新启动仍需执行项目启动命令。

### 添加和管理拼多多店铺

1. 先在“快茴AI客服”中关闭自动发送，避免两个程序竞争同一会话。
2. 在店铺总览点击“添加店铺”。每次添加都会启动一个独立的系统 Chrome 窗口，并打开
   拼多多商家客服页。
3. 在该窗口登录。程序从页面可见 DOM 读取稳定的平台店铺 ID 和店铺名；无法取得稳定 ID
   时保持待识别状态并禁止收发，不会仅凭名称绑定。重复登录已接入账号会被拒绝。
4. 旧店登录状态继续保存在 `data/runtime/pdd-chrome`；新增店铺分别保存到
   `data/runtime/pdd-shops/<profile-key>`。停用店铺不会删除登录目录或业务数据。
5. 进入店铺工作区后选择“人机协同”或“受控自动”。人机协同是默认模式，只生成建议；
   受控自动仍需同时通过全局、会话、审核、风险、置信度和商品上下文门控。

上线店铺会设置 `desired_online=true`，服务重启后以最多两个并发自动恢复窗口；默认最多
同时上线 5 家店铺，可用 `PDD_MAX_ACTIVE_SHOPS` 调整。手动下线会取消尚未点击的排队任务，
但不会删除历史数据。浏览器关闭、登录失效或会话列表关键结构异常时，该店自动发送会
立即失效并有限重启，不影响其他店铺。单个会话发送失败或结果无法确认时，只暂停该会话。
人工发送后对应会话进入人工接管，需在右侧主动恢复自动模式。

旧的无店铺 API 仅在恰好一个 active 店铺时兼容转发；多店状态返回
`409 SHOP_CONTEXT_REQUIRED`。新集成应始终使用 `/api/v1/shops/{shop_id}/...`。

### 智能客服路由与商品资料

控制台收到文本后依次处理：退款、退货、换货、取消订单、补发、赔付、投诉和平台介入
会立即切换为人工接管；纯问候使用店铺问候话术；唯一精确 QA 返回审核标准答案；商品
咨询读取会话商品卡片对应的外部商品资料；其余基础问题进入现有 RAG。商品卡片缺失、
商品服务超时、资料格式无效或商品 ID 不一致时，不会猜测回答，会直接转人工且不发送话术。

商品资料服务默认使用 `GET {PRODUCT_API_BASE_URL}/products/{goods_id}`，可选
`Authorization: Bearer ${PRODUCT_API_BEARER_TOKEN}`。响应可以是顶层对象或 `data` 对象，必须
提供 `id`、`name`、`summary`，并可提供卖点、规格、使用方法、适用人群、注意事项、售后限制
和更新时间。令牌只从本地环境变量读取，不会出现在启动诊断或决策审计中。

商品回答只引用该资料；只有资料完整支持、未涉及敏感/售后、无需追问、模型置信度达到
`PRODUCT_AUTO_REPLY_MIN_CONFIDENCE`（默认 `0.95`）且自动接待已启用时才会自动发送。右侧
“售后转人工”配置可修改自动发送给顾客的固定说明；商品资料缺失类接管不发送该说明。

### RAG 自动回复校准

FAQ 精确命中仍会经过有效性、风险、敏感范围和商品上下文门控。RAG 自动发送还要求
逐条质检标注（不是工作簿中的汇总合格率）完成离线校准。标注文件支持 XLSX、CSV
或 JSONL，必须包含 `query` 和 `expected_qa` 两列；XLSX 默认工作表名为“质检标注”。

```powershell
uv run python scripts/calibrate_auto_reply.py data/qa/质检标注.xlsx
```

只有不少于 `AUTO_REPLY_MIN_SAMPLES`（默认 100）条有效样本，且接受集合精确率的
Wilson 95% 置信区间下界达到 `AUTO_REPLY_MIN_PRECISION`（默认 0.98）时，生成的
`data/runtime/auto_reply_calibration.json` 才会启用 RAG 自动发送。当前整理工作簿只有
聚合质检指标、没有逐条查询标注，因此在补齐标注并运行校准前，RAG 会安全降级为
人工建议。

### Unified Chat 与 Customer Demo

聊天接口：`POST /api/v1/chat`：

```json
{
  "conversation_id": "sess_123",
  "customer_id": "demo_customer",
  "message": "这个一天戴多久？",
  "product_id": "972793561880",
  "service_stage": "pre_sale"
}
```

响应会返回 `source`、`route`、`product_resolution`、`rule_name`、`confidence`、`qa_hit`
和 `sources` 等调试字段。`source` 可能为 `rule`、`product`、`product_selection` 或
`qa`；`product_resolution` 表示商品 ID 来自本次 request、商品 URL、消息文本、
商品名称精确匹配/候选、已有 conversation binding，或没有商品上下文。

`chat_conversation_products` 是 MySQL 中的 conversation-product binding：

```text
conversation_id ↔ product_id
```

表内 `product_name` 只是有限展示标签。完整商品事实不会长期保存在 binding 中；后续追问会
通过 conversation binding 取得 `product_id`，再重新查询 PostgreSQL 商品资料。

`/customer-demo` 默认使用 unified chat transport，即 `POST /api/v1/chat`。页面支持多
Session、每个 Session 的商品上下文隔离、显式 Product ID、消息中的 Product ID、商品 URL
解析、商品名称候选选择、Conversation Product Binding 和 Debug Panel。Debug Panel 展示
`route`、`source`、`rule`、`confidence`、`qa_hit`、`sources`、`product_resolution`、
`current_product_id`、`request_id` 和 latency 等字段。页面仍保留
`QA compatibility + rule preflight` 和 `future rag-chat` 选项用于开发对照，但默认路径不是
`/api/v1/qa`。

知识问答接口：`POST /api/v1/qa`：

```json
{
  "query": "材质是什么？",
  "product_code": "SKU-001",
  "service_stage": "pre_sale",
  "knowledge_version": "2026-09"
}
```

FAQ exact match 的优先级为“商品+当前阶段、商品+general、通用+当前阶段、通用+general”。
同一优先级存在多条结果或缺少必要商品上下文时不会任取一条自动发送，而是进入 RAG
或人工建议。`match_score=1.0` 只表示字符串精确命中，不等于自动发送置信度。

返回的 `data.route` 为 `faq`、`rag` 或 `fallback`。FAQ 精确命中置信度为
`1.0`；RAG V1 不伪造统一置信度，返回 `null`。

默认 `QA_DATA_SOURCE=mysql`，服务初始化时仅加载 `cs_qa` 中当前有效且
`retrieval_enabled=true` 的数据一次，用于 FAQ 和 BM25 内存索引。也可显式设置为
`excel`，从 `QA_EXCEL_PATH` 的 `QA_EXCEL_SHEET` 读取可用记录。

初始化或刷新 Milvus 向量知识库：

```powershell
.venv\Scripts\python.exe scripts/ingest_knowledge.py
```

若需导入 `.md`/`.txt` 知识目录：

```powershell
.venv\Scripts\python.exe scripts/ingest_knowledge.py data/knowledge
```

脚本输出文档数、chunk 数、成功数、失败数和 collection。执行前需要 Milvus
可用，并确保 `EMBEDDING_MODEL_PATH` 或 `EMBEDDING_MODEL` 指向可加载的 BGE 模型。
RAG 查询还需要 `RERANKER_MODEL_PATH` 或 `RERANKER_MODEL` 可用。

索引 chunk 带 document/version/content hash/model/schema/active 元数据。新版本完整写入并
验证后才激活，旧版本随后删除；搜索只返回 active chunk。collection 的向量维度或必要
schema 字段不兼容时脚本会拒绝复用，需提升 `MILVUS_COLLECTION` 版本名。

`RAG_SCORE_THRESHOLD` 必须结合业务测试集与所用 reranker 分数分布调优，示例值
不是通用最佳阈值。

成功响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "answer": "您好，请问有什么可以帮您？"
  }
}
```

## 日志定位

日志按本地日期写入统一目录，服务运行中跨天自动切换，无需重启：

- `logs/YYYY-MM-DD/app.log`：全部日志
- `logs/YYYY-MM-DD/error.log`：仅 ERROR 及以上日志
- `logs/YYYY-MM-DD/access.log`：HTTP 访问日志
- `logs/YYYY-MM-DD/qa.log`：QA/RAG 日志
- `logs/YYYY-MM-DD/connector.log`：PDD/Console 连接器日志
- `logs/YYYY-MM-DD/audit.log`：预留审计日志

日期目录默认保留 30 天，可用 `LOG_RETENTION_DAYS` 调整。文件内容采用 JSON
Lines。每条记录包含 `timestamp`、`level`、`logger`、`module`、`request_id`、
`trace_id`、`conversation_id`、`shop_id`、`source_file`、`source_line` 和
`source_function`。HTTP 响应头会返回 `X-Request-ID` 与 `X-Trace-ID`；未显式传入
trace 时二者相同。错误记录包含完整 `traceback`，但客户端只收到统一错误信息，
不会看到 Python 堆栈。密码、Token、Authorization、Cookie 等敏感字段会在日志层
统一脱敏，访问日志也不记录请求体或查询参数。


### Logger 路由规则

| Logger 前缀 / 名称 | 专项目志 | 同时写入 `app.log` | 说明 |
| --- | --- | --- | --- |
| root 及其他未命中规则 logger | - | 是 | 全量应用与框架日志 |
| `app.middleware.logging` | `access.log` | 是 | HTTP 访问日志 |
| `app.qa` | `qa.log` | 是 | QA/RAG 及其子模块 |
| `app.integrations.pdd` | `connector.log` | 是 | PDD 连接器 |
| `app.services.console_runtime` | `connector.log` | 是 | Console 连接运行时 |
| `app.services.shop_runtime_manager` | `connector.log` | 是 | 多店铺连接器管理 |
| `app.audit` | `audit.log` | 是 | 预留审计 logger |
| 任意 logger 的 `ERROR/CRITICAL` | `error.log` | 是 | 双写错误分流 |

日志配置会显式保持专项目志 logger 的 `propagate=True`，让分类日志继续传播到 root；
同一条日志会在专项目志
和 `app.log` 各出现一次，但不会在同一个文件内重复出现。`event`、`method`、`path`、
`status_code`、`duration_ms`、`model` 等高频字段只保留在 JSON 顶层，其余 extra 字段
放在 `fields`。

### 标准排障 SOP

1. 先确定日志日期。服务使用本地日期目录；跨天请求分别查看前后两个日期目录。
2. 优先从 `error.log` 找错误原因和 traceback。
3. 用 `conversation_id` 汇总会话内所有请求，再取其中异常请求的 `request_id`。
4. 用 `request_id` 精确还原单次 HTTP / service / QA / connector 链路。
5. 如调用方或下游服务单独传入 trace，用 `trace_id` 做跨服务关联；未显式传入时它与
   `request_id` 相同。

```bash
LOG_DATE=$(date +%F)

# 1. 先看当日 ERROR / CRITICAL 和完整 traceback
rg -n '"level": "(ERROR|CRITICAL)"' "logs/${LOG_DATE}/error.log"

# 2. 已知错误事件时直接按 event 查，例如未处理异常
rg -n -F '"event": "unhandled_exception"' "logs/${LOG_DATE}/error.log"

# 3. 按会话定位完整链路，并从结果中提取异常 request_id
rg -n -F '"conversation_id": "conv-123"' "logs/${LOG_DATE}"

# 4. 按单次请求精确排查 HTTP、service、QA、connector 与异常日志
rg -n -F '"request_id": "req-123"' "logs/${LOG_DATE}"

# 5. 需要跨服务/跨进程关联时按 trace 查询；默认等于 request_id
rg -n -F '"trace_id": "trace-123"' "logs/${LOG_DATE}"

# 不确定具体日期时全量检索
rg -n -F '"request_id": "req-123"' logs
```

`rg` 默认输出文件名和行号。若需要上下文，可追加 `-C 3`；若只查错误文件，可将目标限定为
`logs/${LOG_DATE}/error.log`。

## 测试

首次准备或 CI 环境安装：

```powershell
uv sync --frozen
```

```powershell
uv run pytest -q
```

静态检查：

```powershell
uv run ruff check .
```

迁移链可用隔离 SQLite 数据库快速验证：

```powershell
$env:DATABASE_URL="sqlite+aiosqlite:///./migration-test.db"
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
Remove-Item Env:DATABASE_URL
```

基础设施配置检查：

```powershell
docker compose config --quiet
uv run python scripts/dev_compose.py info
```

GitHub Actions 已建立在 `.github/workflows/ci.yml` 中，会在 pull request 和 `master`
push 时执行 `lint` 与 `test` 两个 job：

```powershell
uv sync --frozen
uv run ruff check .
uv run pytest -q
```

是否将这些 job 配置为 required status checks 由 GitHub repository settings 决定，仓库内
CI 文件本身不代表分支保护已经启用。

### 常见问题

- `cs_qa` 为空：这是清理后的安全状态；新增 QA 时执行 migration 并用新的已审核工作簿运行导入脚本。
- Milvus schema incompatible：提升 `MILVUS_COLLECTION` 版本名后重新索引，不要复用旧表。
- RAG 只给人工建议：检查校准文件的样本数和 Wilson 下界；这是默认安全行为。
- 拼多多连接器未就绪：重新打开专用 Chrome 并确认已登录，不要复制 Cookie 到配置。

测试通过依赖覆盖和 Fake LLM/Retriever 执行，不会向真实模型、Embedding API 或
远程向量库发送请求。

## 当前边界

当前仍面向本地客服中台部署，暂不读取订单详情、物流轨迹或退款进度，不处理图片理解、
语音转写、多客服分配和公网部署。页面自动化
可能随拼多多页面升级而需要更新 `app/integrations/pdd/selectors.py`；业务层通过
`CustomerServiceConnector` 接口隔离，后续可替换为官方连接器。

`NEEDS_PRODUCT_DECISION`：当前 QA 工作簿没有逐条“允许无人值守自动发送”字段，所有
导入条目保持 `auto_reply_eligible=false`。产品/质检团队定义并填写该授权字段前，不应
批量开启 FAQ 自动发送。
