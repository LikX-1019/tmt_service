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
