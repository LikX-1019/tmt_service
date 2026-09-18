# Customer Service Agent

电商智能客服项目。当前包含拼多多多店铺本地客服中台、每店独立 Chrome 页面连接器、
会话与发送任务持久化、SSE 实时更新，以及 FAQ + Hybrid RAG 知识问答模块。

拼多多接入只操作页面上可见的 DOM，不调用未公开 HTTP 接口，也不解析平台私有
WebSocket 帧。每个店铺使用独立发送队列并验证己方消息气泡；点击后无法确认的任务会进入
`uncertain`，不会自动重试。

QA 模块由 `QAService` 统一编排，不依赖 FastAPI 或 LangGraph。它优先执行 FAQ
精确匹配，未命中时并行执行 Milvus Dense Search 与内存 BM25，使用 RRF 融合、
BGE Reranker 和证据阈值后生成答案；证据不足时直接返回固定 fallback。

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

先启动数据服务并执行数据库迁移：

```powershell
docker compose up -d mysql etcd minio milvus
uv run alembic upgrade head
```

Alembic 是所有正式表（包括 `cs_qa`、会话、消息、发送任务和审计事件）的唯一 schema
来源。全新数据库和现有开发库都使用同一条迁移链，不通过应用启动时 `create_all()` 建表。

首次迁移后导入清洗后的 QA 工作簿：

```powershell
powershell -File scripts/import_cleaned_qa.ps1
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

客服控制台：启动服务后访问 `http://127.0.0.1:8000/`。页面无需安装 Node.js 或
执行前端构建。原聊天测试页移动到 `http://127.0.0.1:8000/chat-demo`。

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

聊天接口：`POST /api/v1/chat`：

```json
{
  "message": "你好"
}
```

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

每个服务进程启动时生成两个带时间和进程号的文件：

- `logs/app-YYYYMMDD-HHMMSS-PID.log`：全部日志
- `logs/error-YYYYMMDD-HHMMSS-PID.log`：仅 ERROR 及以上日志

文件内容采用 JSON Lines。每条记录包含 `timestamp`、`level`、`logger`、
`source_file`、`source_line`、`source_function` 和 `request_id`。HTTP 响应头
会返回 `X-Request-ID`；用它在日志中搜索即可串起一次请求。错误记录包含完整
`traceback`，但客户端只收到统一错误信息，不会看到 Python 堆栈。

## 测试

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
```

### 常见问题

- `cs_qa` 为空：先执行 migration，再运行 `scripts/import_cleaned_qa.ps1`。
- Milvus schema incompatible：提升 `MILVUS_COLLECTION` 版本名后重新索引，不要复用旧表。
- RAG 只给人工建议：检查校准文件的样本数和 Wilson 下界；这是默认安全行为。
- 拼多多连接器未就绪：重新打开专用 Chrome 并确认已登录，不要复制 Cookie 到配置。

测试通过依赖覆盖和 Fake LLM/Retriever 执行，不会向真实模型、Embedding API 或
远程向量库发送请求。

## 当前边界

首版只支持一个店铺、一台 Windows 电脑和一个本地客服用户。暂不读取订单详情、物流
轨迹或退款进度，不处理图片理解、语音转写、多店铺、多客服分配和公网部署。页面自动化
可能随拼多多页面升级而需要更新 `app/integrations/pdd/selectors.py`；业务层通过
`CustomerServiceConnector` 接口隔离，后续可替换为官方连接器。

`NEEDS_PRODUCT_DECISION`：当前 QA 工作簿没有逐条“允许无人值守自动发送”字段，所有
导入条目保持 `auto_reply_eligible=false`。产品/质检团队定义并填写该授权字段前，不应
批量开启 FAQ 自动发送。
