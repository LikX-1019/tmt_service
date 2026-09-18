# 商品 PostgreSQL 数据库

商品资料使用独立的 `product-postgres` 容器，不与客服业务 MySQL 混用。数据库仅监听
`127.0.0.1`，持久化目录为 `data/docker/product-postgres`。

## 启动

先在 `.env` 中设置强密码，再启动容器：

```sh
docker compose pull product-postgres
docker compose up -d product-postgres
docker compose ps product-postgres
```

首次创建数据目录时，PostgreSQL 会自动执行
`docker/product-postgres/init/001_product_catalog.sql`。该脚本创建：

- `products`：商品主资料，主键 `id` 必须使用平台商品卡片的 `goods_id`。
- `product_variants`：SKU、规格组合、价格及库存。
- `product_api_profiles`：只暴露已发布商品，并与现有商品 API 字段保持一致。

初始化脚本只会在空数据目录首次启动时执行。以后修改结构应通过正式迁移脚本处理，
不要删除数据目录来重新初始化。

## 连接

宿主机连接参数：

```text
Host: 127.0.0.1
Port: PRODUCT_POSTGRES_PORT，默认 5432
Database: PRODUCT_POSTGRES_DB，默认 product_catalog
User: PRODUCT_POSTGRES_USER，默认 product_catalog
Password: PRODUCT_POSTGRES_PASSWORD
```

也可以直接进入容器：

```sh
docker compose exec product-postgres sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## 新增商品示例

只有 `status = 'published'` 的商品才会出现在 API 视图中：

```sql
INSERT INTO products (
    id, internal_code, name, summary, selling_points, specifications,
    usage, suitable_for, warnings, after_sales_limits, status
) VALUES (
    '972793561880',
    'BG07',
    'JAFFICK运动护腕支架 Pro',
    '用于日常运动及办公场景下的手腕支撑。',
    '["三段式可调节绑带", "左右手均可佩戴"]'::jsonb,
    '{"颜色":"黑色", "尺码":"均码", "材质":"锦纶、聚酯纤维、氨纶"}'::jsonb,
    '松开绑带并调整至舒适松紧度。',
    '适合日常运动、办公及一般手腕支撑需求。',
    '出现麻木、红肿或疼痛加重时立即停止使用。',
    '按店铺及平台公示规则处理。',
    'published'
);
```

验证供 API 使用的数据：

```sql
SELECT * FROM product_api_profiles WHERE id = '972793561880';
```

## 备份

```sh
docker compose exec -T product-postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > product_catalog.sql
```

数据库容器只负责存储。`commodity_management` 子系统提供运营管理页面，并通过
`GET {PRODUCT_API_BASE_URL}/products/{goods_id}` 向客服程序提供已发布的商品资料。
