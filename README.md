# Customer Service Agent

电商智能客服项目。当前包含拼多多单店本地客服控制台、专用 Chrome 页面连接器、
会话与发送任务持久化、SSE 实时更新，以及 FAQ + Hybrid RAG 知识问答模块。

拼多多接入只操作页面上可见的 DOM，不调用未公开 HTTP 接口，也不解析平台私有
WebSocket 帧。发送采用单队列并验证己方消息气泡；点击后无法确认的任务会进入
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
uv sync --extra dev
```

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
docker compose up -d mysql redis etcd minio milvus
uv run alembic upgrade head
```

`cs_qa` 仍由原初始化/导入脚本维护，Alembic 迁移只新增客服控制台相关表，不修改
现有知识库数据。

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

### 首次连接拼多多

1. 先在“快茴AI客服”中关闭自动发送，避免两个程序竞争同一会话。
2. 在本项目首页点击“打开登录窗口”。程序会启动独立的系统 Chrome，并打开拼多多
   商家客服页。
3. 在这个专用 Chrome 中扫码登录。登录状态仅保存到
   `data/runtime/pdd-chrome`，程序不保存账号密码，也不会读取现有工作台的 Cookie。
4. 状态变为“消息监听正常”后可人工发送。全局自动回复和会话自动回复是双重开关；
   首次安装全局开关默认关闭。

浏览器关闭、登录失效、关键选择器异常或发送结果无法确认时，自动发送会立即失效。
人工发送后对应会话进入人工接管，需在右侧主动恢复自动模式。

### RAG 自动回复校准

FAQ 精确命中仍会经过有效性、风险、敏感范围和商品上下文门控。RAG 自动发送还要求
逐条质检标注（不是工作簿中的汇总合格率）完成离线校准。标注文件支持 XLSX、CSV
或 JSONL，必须包含 `query` 和 `expected_qa` 两列；XLSX 默认工作表名为“质检标注”。

```powershell
uv run python scripts/calibrate_auto_reply.py data/qa/质检标注.xlsx
```

只有不少于 `AUTO_REPLY_MIN_SAMPLES`（默认 100）条有效样本，且接受集合精确率达到
`AUTO_REPLY_MIN_PRECISION`（默认 0.98）时，生成的
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
  "query": "支持七天无理由吗？"
}
```

返回的 `data.route` 为 `faq`、`rag` 或 `fallback`。FAQ 精确命中置信度为
`1.0`；RAG V1 不伪造统一置信度，返回 `null`。

默认 `QA_DATA_SOURCE=mysql`，服务初始化时仅加载 `cs_qa` 中当前有效的
`published + usable` 数据一次，用于 FAQ 和 BM25 内存索引。也可显式设置为
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

测试通过依赖覆盖和 Fake LLM/Retriever 执行，不会向真实模型、Embedding API 或
远程向量库发送请求。

## 当前边界

首版只支持一个店铺、一台 Windows 电脑和一个本地客服用户。暂不读取订单详情、物流
轨迹或退款进度，不处理图片理解、语音转写、多店铺、多客服分配和公网部署。页面自动化
可能随拼多多页面升级而需要更新 `app/integrations/pdd/selectors.py`；业务层通过
`CustomerServiceConnector` 接口隔离，后续可替换为官方连接器。
