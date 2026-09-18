\set ON_ERROR_STOP on

BEGIN;
\ir /opt/product-postgres/product_api_profiles.sql
COMMENT ON VIEW product_api_profiles IS
    '供商品客服 API 返回的已发布商品事实字段；由 002 扩展只读 profile';
COMMIT;
