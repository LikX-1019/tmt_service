from __future__ import annotations

from typing import Any

from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.schemas import ProductCreate, ProductUpdate, VariantCreate, VariantUpdate


settings = get_settings()
pool = ConnectionPool(
    conninfo=settings.database_dsn(),
    min_size=1,
    max_size=10,
    open=False,
    kwargs={"row_factory": dict_row},
)


PRODUCT_COLUMNS = """
    p.id, p.platform, p.internal_code, p.name, p.summary, p.brand,
    p.category_name, p.model, p.selling_points, p.specifications, p.usage,
    p.suitable_for, p.warnings, p.after_sales_limits, p.compliance_notes,
    p.status, p.owner_name, p.reviewed_by, p.effective_at, p.published_at,
    p.created_at, p.updated_at
"""


def open_pool() -> None:
    pool.open(wait=True, timeout=15)


def close_pool() -> None:
    pool.close()


def _product_values(data: ProductCreate | ProductUpdate) -> tuple[Any, ...]:
    return (
        data.platform,
        data.internal_code,
        data.name,
        data.summary,
        data.brand,
        data.category_name,
        data.model,
        Jsonb(data.selling_points),
        Jsonb(data.specifications),
        data.usage,
        data.suitable_for,
        data.warnings,
        data.after_sales_limits,
        data.compliance_notes,
        data.status,
        data.owner_name,
        data.reviewed_by,
        data.effective_at,
        data.published_at,
    )


def list_products(search: str, status: str, page: int, page_size: int) -> dict[str, Any]:
    pattern = f"%{search.strip()}%"
    conditions = [
        "(%s = '' OR p.id ILIKE %s OR p.name ILIKE %s OR COALESCE(p.internal_code, '') ILIKE %s)",
        "(%s = 'all' OR p.status = %s)",
    ]
    params: tuple[Any, ...] = (search.strip(), pattern, pattern, pattern, status, status)
    where_clause = " AND ".join(conditions)
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) AS total FROM products p WHERE {where_clause}", params)
            total = cursor.fetchone()["total"]
            cursor.execute(
                f"""
                SELECT {PRODUCT_COLUMNS}, count(v.sku_id)::int AS variant_count
                FROM products p
                LEFT JOIN product_variants v ON v.product_id = p.id
                WHERE {where_clause}
                GROUP BY p.id
                ORDER BY p.updated_at DESC, p.id
                LIMIT %s OFFSET %s
                """,
                (*params, page_size, (page - 1) * page_size),
            )
            items = cursor.fetchall()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_product(product_id: str) -> dict[str, Any] | None:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {PRODUCT_COLUMNS}, count(v.sku_id)::int AS variant_count
                FROM products p
                LEFT JOIN product_variants v ON v.product_id = p.id
                WHERE p.id = %s
                GROUP BY p.id
                """,
                (product_id,),
            )
            return cursor.fetchone()


def get_published_profile(product_id: str) -> dict[str, Any] | None:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM product_api_profiles WHERE id = %s",
                (product_id,),
            )
            return cursor.fetchone()


def search_published_profiles(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """按真实 ID、名称和内部编码搜索已发布商品，精确结果优先。"""
    normalized = query.strip()
    if not normalized:
        return []
    pattern = f"%{normalized}%"
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT profile.*,
                       CASE
                           WHEN lower(profile.id) = lower(%s)
                             OR lower(profile.name) = lower(%s)
                             OR lower(COALESCE(profile.internal_code, '')) = lower(%s)
                           THEN 'exact'
                           ELSE 'contains'
                       END AS match_type
                FROM product_api_profiles AS profile
                WHERE profile.name ILIKE %s
                   OR COALESCE(profile.internal_code, '') ILIKE %s
                   OR profile.id ILIKE %s
                ORDER BY
                    CASE
                        WHEN lower(profile.id) = lower(%s) THEN 0
                        WHEN lower(profile.name) = lower(%s) THEN 1
                        WHEN lower(COALESCE(profile.internal_code, '')) = lower(%s) THEN 2
                        ELSE 3
                    END,
                    profile.name,
                    profile.id
                LIMIT %s
                """,
                (
                    normalized,
                    normalized,
                    normalized,
                    pattern,
                    pattern,
                    pattern,
                    normalized,
                    normalized,
                    normalized,
                    min(max(limit, 1), 5),
                ),
            )
            return cursor.fetchall()


def create_product(data: ProductCreate) -> dict[str, Any]:
    sql = """
        INSERT INTO products (
            id, platform, internal_code, name, summary, brand, category_name,
            model, selling_points, specifications, usage, suitable_for, warnings,
            after_sales_limits, compliance_notes, status, owner_name, reviewed_by,
            effective_at, published_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (data.id, *_product_values(data)))
    return get_product(data.id)  # type: ignore[return-value]


def update_product(product_id: str, data: ProductUpdate) -> dict[str, Any] | None:
    sql = """
        UPDATE products SET
            platform = %s, internal_code = %s, name = %s, summary = %s,
            brand = %s, category_name = %s, model = %s, selling_points = %s,
            specifications = %s, usage = %s, suitable_for = %s, warnings = %s,
            after_sales_limits = %s, compliance_notes = %s, status = %s,
            owner_name = %s, reviewed_by = %s, effective_at = %s, published_at = %s
        WHERE id = %s
    """
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (*_product_values(data), product_id))
            if cursor.rowcount == 0:
                return None
    return get_product(product_id)


def delete_product(product_id: str) -> bool:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
            return cursor.rowcount > 0


def list_variants(product_id: str) -> list[dict[str, Any]]:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT sku_id, product_id, name, attributes, currency, price,
                       stock_status, stock_quantity, status, created_at, updated_at
                FROM product_variants
                WHERE product_id = %s
                ORDER BY updated_at DESC, sku_id
                """,
                (product_id,),
            )
            return cursor.fetchall()


def create_variant(product_id: str, data: VariantCreate) -> dict[str, Any]:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO product_variants (
                    sku_id, product_id, name, attributes, currency, price,
                    stock_status, stock_quantity, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING sku_id, product_id, name, attributes, currency, price,
                          stock_status, stock_quantity, status, created_at, updated_at
                """,
                (
                    data.sku_id,
                    product_id,
                    data.name,
                    Jsonb(data.attributes),
                    data.currency,
                    data.price,
                    data.stock_status,
                    data.stock_quantity,
                    data.status,
                ),
            )
            return cursor.fetchone()


def update_variant(sku_id: str, data: VariantUpdate) -> dict[str, Any] | None:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE product_variants SET
                    name = %s, attributes = %s, currency = %s, price = %s,
                    stock_status = %s, stock_quantity = %s, status = %s
                WHERE sku_id = %s
                RETURNING sku_id, product_id, name, attributes, currency, price,
                          stock_status, stock_quantity, status, created_at, updated_at
                """,
                (
                    data.name,
                    Jsonb(data.attributes),
                    data.currency,
                    data.price,
                    data.stock_status,
                    data.stock_quantity,
                    data.status,
                    sku_id,
                ),
            )
            return cursor.fetchone()


def delete_variant(sku_id: str) -> bool:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM product_variants WHERE sku_id = %s", (sku_id,))
            return cursor.rowcount > 0


def existing_product_ids(product_ids: set[str]) -> set[str]:
    if not product_ids:
        return set()
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM products WHERE id = ANY(%s)", (list(product_ids),))
            return {row["id"] for row in cursor.fetchall()}


def bulk_upsert_catalog(
    products: list[ProductCreate],
    variants: list[tuple[str, VariantCreate]],
) -> dict[str, int]:
    product_ids = [product.id for product in products]
    sku_ids = [variant.sku_id for _product_id, variant in variants]
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            existing_products: set[str] = set()
            existing_skus: set[str] = set()
            if product_ids:
                cursor.execute("SELECT id FROM products WHERE id = ANY(%s)", (product_ids,))
                existing_products = {row["id"] for row in cursor.fetchall()}
            if sku_ids:
                cursor.execute("SELECT sku_id FROM product_variants WHERE sku_id = ANY(%s)", (sku_ids,))
                existing_skus = {row["sku_id"] for row in cursor.fetchall()}

            for product in products:
                cursor.execute(
                    """
                    INSERT INTO products (
                        id, platform, internal_code, name, summary, brand, category_name,
                        model, selling_points, specifications, usage, suitable_for, warnings,
                        after_sales_limits, compliance_notes, status, owner_name, reviewed_by,
                        effective_at, published_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        platform = EXCLUDED.platform,
                        internal_code = EXCLUDED.internal_code,
                        name = EXCLUDED.name,
                        summary = EXCLUDED.summary,
                        brand = EXCLUDED.brand,
                        category_name = EXCLUDED.category_name,
                        model = EXCLUDED.model,
                        selling_points = EXCLUDED.selling_points,
                        specifications = EXCLUDED.specifications,
                        usage = EXCLUDED.usage,
                        suitable_for = EXCLUDED.suitable_for,
                        warnings = EXCLUDED.warnings,
                        after_sales_limits = EXCLUDED.after_sales_limits,
                        compliance_notes = EXCLUDED.compliance_notes,
                        status = EXCLUDED.status,
                        owner_name = EXCLUDED.owner_name,
                        reviewed_by = EXCLUDED.reviewed_by,
                        effective_at = EXCLUDED.effective_at,
                        published_at = EXCLUDED.published_at
                    """,
                    (product.id, *_product_values(product)),
                )

            for product_id, variant in variants:
                cursor.execute(
                    """
                    INSERT INTO product_variants (
                        sku_id, product_id, name, attributes, currency, price,
                        stock_status, stock_quantity, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sku_id) DO UPDATE SET
                        product_id = EXCLUDED.product_id,
                        name = EXCLUDED.name,
                        attributes = EXCLUDED.attributes,
                        currency = EXCLUDED.currency,
                        price = EXCLUDED.price,
                        stock_status = EXCLUDED.stock_status,
                        stock_quantity = EXCLUDED.stock_quantity,
                        status = EXCLUDED.status
                    """,
                    (
                        variant.sku_id,
                        product_id,
                        variant.name,
                        Jsonb(variant.attributes),
                        variant.currency,
                        variant.price,
                        variant.stock_status,
                        variant.stock_quantity,
                        variant.status,
                    ),
                )

    return {
        "products_created": len(set(product_ids) - existing_products),
        "products_updated": len(set(product_ids) & existing_products),
        "variants_created": len(set(sku_ids) - existing_skus),
        "variants_updated": len(set(sku_ids) & existing_skus),
    }


def dashboard_stats() -> dict[str, int]:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*)::int AS total,
                    count(*) FILTER (WHERE status = 'published')::int AS published,
                    count(*) FILTER (WHERE status = 'draft')::int AS draft,
                    count(*) FILTER (WHERE status = 'offline')::int AS offline
                FROM products
                """
            )
            result = cursor.fetchone()
            cursor.execute("SELECT count(*)::int AS variants FROM product_variants")
            result["variants"] = cursor.fetchone()["variants"]
            return result


__all__ = [
    "errors",
    "open_pool",
    "close_pool",
    "list_products",
    "get_product",
    "get_published_profile",
    "search_published_profiles",
    "create_product",
    "update_product",
    "delete_product",
    "list_variants",
    "create_variant",
    "update_variant",
    "delete_variant",
    "existing_product_ids",
    "bulk_upsert_catalog",
    "dashboard_stats",
]
