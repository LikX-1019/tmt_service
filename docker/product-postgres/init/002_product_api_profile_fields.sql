BEGIN;

CREATE OR REPLACE VIEW product_api_profiles AS
SELECT
    id,
    name,
    summary,
    selling_points,
    specifications,
    usage,
    suitable_for,
    warnings,
    after_sales_limits,
    updated_at,
    platform,
    internal_code,
    brand,
    category_name,
    model,
    compliance_notes
FROM products
WHERE status = 'published';

COMMENT ON VIEW product_api_profiles IS
    '供商品客服 API 返回的已发布商品事实字段；由 002 扩展只读 profile';

COMMIT;
