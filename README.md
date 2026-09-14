# Customer Service Agent

电商智能客服项目。当前已完成第一阶段可运行切片：FastAPI、配置管理、
OpenAI-compatible LLM Chat、统一响应和异常、可追踪日志、Factory 与测试。

仓库中已有 RAG、Tool Calling、LangGraph、Memory、数据库等后续阶段目录，
这些目录目前大多是占位模块，不属于第一阶段的可用能力。

## 环境要求

- Python 3.11+
- 推荐使用 [uv](https://docs.astral.sh/uv/)

## 安装

```powershell
uv sync --extra dev
```

也可以使用 pip：

```powershell
python -m pip install -e ".[dev]"
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

## 启动

```powershell
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

不使用 uv 时：

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：`GET /health`。

聊天接口：`POST /api/v1/chat`：

```json
{
  "message": "你好"
}
```

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

测试通过依赖覆盖和 Fake LLM 执行，不会向真实模型服务发送请求。

## 当前边界

第一阶段暂未接通 RAG、Tool Calling、LangGraph、Memory、Redis、MySQL、
向量数据库、Embedding 和 Reranker。下一阶段接入 RAG 时，建议从
`app/rag/ingestion` 和 `app/rag/retrieval` 实现独立检索服务，再由
`ChatService` 或后续 Agent 编排层调用，避免将知识库逻辑写进 API 层。
