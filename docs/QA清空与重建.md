# QA 旧知识清空与重建

## 本次确认的数据位置

- 运行时正式 QA 数据：应用 MySQL 表 `cs_qa`。
- 向量化副本：Milvus collection `MILVUS_COLLECTION`（默认 `customer_service_knowledge_v1`）。
- 商品事实：独立 PostgreSQL 数据库 `products` 表，通过 commodity-management 的
  `GET /products/{goods_id}` 只读发布视口访问；商品问答不再读取 `cs_qa`。

## 安全清理

Codex 不直接连接并删除真实数据库。确认连接环境和备份策略后由管理员执行：

```sh
uv run python scripts/clear_qa_knowledge.py
# 确认输出中的 MySQL / Milvus 目标无误后：
uv run python scripts/clear_qa_knowledge.py --yes --backup ./data/runtime/qa-backup.json
```

脚本行为：

- 可先执行 dry-run，不加 `--yes` 时不会修改数据。
- `DELETE FROM cs_qa`，不 DROP 表、不修改字段、不删除 QA API / Service / Repository。
- 删除 Milvus collection 中的旧 entities，不 DROP collection，索引和字段结构保留。
- `--skip-milvus` 可跳过向量清理，但会留下旧向量召回风险，不建议使用。

## 空知识库行为

`QAService` 在目录为空时直接返回既定 fallback：

```text
抱歉，目前知识库中没有找到足够的信息来准确回答这个问题。你可以提供更具体的商品名称或问题描述，我再帮你查询。
```

不会调用 QA LLM，也不会凭常识回答业务问题。

## 重新添加 QA

1. 准备新的审核后 QA 数据源。
2. 使用现有导入 / 索引能力重建 `cs_qa` 与 Milvus：

```sh
uv run python scripts/import_cleaned_qa.py --help
uv run python scripts/ingest_knowledge.py
```

3. 重启服务，或在新增部署中让 `get_qa_service()` 重新加载目录。
