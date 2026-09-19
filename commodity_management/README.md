# 商品资料管理系统

这是与客服主应用隔离的商品管理子系统，提供：

- 商品资料的新增、查询、编辑、删除、搜索、状态筛选和分页。
- 动态规格以及 SKU、价格、库存的增删改查。
- Excel 批量导入：商品表每行一件商品，SKU 表每行一个 SKU。
- 登录会话保护和删除二次确认。
- 与客服现有契约兼容的 `GET /products/{goods_id}` 只读接口。
- 桌面与移动端自适应页面，支持同一局域网访问。

## 启动

所有密码和令牌均从项目根目录的 `.env` 读取：

```sh
docker compose up -d --build product-postgres commodity-management
docker compose ps product-postgres commodity-management
```

本机打开：<http://127.0.0.1:8088>

局域网用户打开：`http://本机局域网IP:8088`。macOS 可在“系统设置 → 网络”中查看 IP，
也可运行：

```sh
ipconfig getifaddr en0
```

如果其他设备无法访问，请允许 Docker Desktop 接受系统防火墙的入站连接，并确认双方在
同一个局域网。不要把 8088 端口直接映射到公网。

## 账号与安全

登录账号来自根目录 `.env`：

```text
COMMODITY_ADMIN_USERNAME
COMMODITY_ADMIN_PASSWORD
```

修改账号、密码或会话密钥后重建容器环境：

```sh
docker compose up -d --force-recreate commodity-management
```

管理页面使用签名 Cookie 会话；数据库端口仍只绑定 `127.0.0.1`，局域网用户不能直接访问
PostgreSQL。当前局域网部署使用 HTTP，不应在不可信网络传输账号密码；如需跨网络或公网
访问，应在前方配置 HTTPS 反向代理、访问控制和备份策略。

## 填写指南与 Excel 批量导入

点击“新建商品”或打开已有商品后，桌面端会在编辑表单左侧显示完整填写指南；窄屏设备可
点击表单顶部的“填写指南”按钮查看。

批量导入步骤：

1. 点击商品库右上角的“Excel 批量导入”。
2. 下载系统模板，不要修改工作表名称或表头。
3. 在“商品导入”中填写商品，每行一件；如需维护 SKU，在“SKU导入”中每行填写一个 SKU，
   并用“平台商品 ID”关联商品。
4. 上传 `.xlsx` 文件并查看校验结果。

模板中带 `*` 的红色表头是必填字段，对应输入列显示为浅黄色；漏填时该单元格会自动红色
高亮。“字段说明”工作表的“是否必填”列也会同步标记为“必填”或“选填”。

模板内的“填写示例”和“字段说明”工作表只用于参考，不会导入。单次文件最大 5 MB，最多
1000 件商品和 1000 个 SKU。系统会先校验整份文件：只要有一行错误，本次就不会写入任何
数据，并会返回工作表、行号和错误原因。已有商品 ID 或 SKU ID 会更新，新 ID 会新增。

## 客服商品接口

只有状态为“已发布”的商品可以通过此接口读取：

```http
GET /products/{goods_id}
Authorization: Bearer ${PRODUCT_API_BEARER_TOKEN}
```

根目录 `.env` 中可配置客服主程序访问本机商品服务：

```text
PRODUCT_API_BASE_URL=http://127.0.0.1:8088
PRODUCT_API_BEARER_TOKEN=<与管理容器一致的令牌>
```

## 目录

```text
commodity_management/
├── app/
│   ├── main.py          # 管理 API、登录和客服只读 API
│   ├── database.py      # PostgreSQL 参数化查询
│   ├── schemas.py       # 输入输出校验
│   └── static/          # 独立管理页面
├── Dockerfile
├── package.json         # 固定版本的 Lucide 图标资源
└── requirements.txt
```
