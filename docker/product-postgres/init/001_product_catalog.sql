BEGIN;

CREATE TABLE IF NOT EXISTS products (
    id VARCHAR(64) PRIMARY KEY,
    platform VARCHAR(32) NOT NULL DEFAULT 'pdd',
    internal_code VARCHAR(64),
    name VARCHAR(500) NOT NULL CHECK (btrim(name) <> ''),
    summary VARCHAR(4000) NOT NULL CHECK (btrim(summary) <> ''),
    brand VARCHAR(200),
    category_name VARCHAR(500),
    model VARCHAR(200),
    selling_points JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(selling_points) = 'array'),
    specifications JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(specifications) = 'object'),
    usage VARCHAR(4000),
    suitable_for VARCHAR(2000),
    warnings VARCHAR(2000),
    after_sales_limits VARCHAR(2000),
    compliance_notes TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'offline')),
    owner_name VARCHAR(100),
    reviewed_by VARCHAR(100),
    effective_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_products_platform_internal_code
    ON products (platform, internal_code)
    WHERE internal_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_products_status ON products (status);
CREATE INDEX IF NOT EXISTS ix_products_name ON products (name);

CREATE TABLE IF NOT EXISTS product_variants (
    sku_id VARCHAR(64) PRIMARY KEY,
    product_id VARCHAR(64) NOT NULL
        REFERENCES products (id) ON UPDATE CASCADE ON DELETE CASCADE,
    name VARCHAR(500) NOT NULL CHECK (btrim(name) <> ''),
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(attributes) = 'object'),
    currency CHAR(3) NOT NULL DEFAULT 'CNY',
    price NUMERIC(12, 2) CHECK (price IS NULL OR price >= 0),
    stock_status VARCHAR(20) NOT NULL DEFAULT 'unknown'
        CHECK (stock_status IN ('unknown', 'in_stock', 'low_stock', 'out_of_stock')),
    stock_quantity INTEGER CHECK (stock_quantity IS NULL OR stock_quantity >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_product_variants_product_id
    ON product_variants (product_id);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_products_updated_at ON products;
CREATE TRIGGER trg_products_updated_at
BEFORE UPDATE ON products
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_product_variants_updated_at ON product_variants;
CREATE TRIGGER trg_product_variants_updated_at
BEFORE UPDATE ON product_variants
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

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
    updated_at
FROM products
WHERE status = 'published';

COMMENT ON TABLE products IS '运营维护的商品主资料，id 使用平台商品 goods_id';
COMMENT ON TABLE product_variants IS '商品 SKU、价格和库存资料';
COMMENT ON VIEW product_api_profiles IS '供现有商品 API 返回的已发布商品字段';

COMMIT;
