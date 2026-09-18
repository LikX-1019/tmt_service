from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg import errors
from starlette.middleware.sessions import SessionMiddleware

from app import database
from app.config import get_settings
from app.excel_service import ImportErrorItem, VARIANT_SHEET, build_template, parse_catalog
from app.schemas import (
    DashboardStats,
    LoginRequest,
    ProductCreate,
    ProductListResponse,
    ProductRead,
    ProductUpdate,
    VariantCreate,
    VariantRead,
    VariantUpdate,
)


settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.open_pool()
    try:
        yield
    finally:
        database.close_pool()


app = FastAPI(
    title="商品资料管理",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.commodity_session_secret,
    session_cookie="commodity_session",
    max_age=8 * 60 * 60,
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


def require_auth(request: Request) -> str:
    username = request.session.get("username")
    if not request.session.get("authenticated") or not isinstance(username, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return username


AdminUser = Annotated[str, Depends(require_auth)]
MAX_EXCEL_BYTES = 5 * 1024 * 1024


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request) -> dict[str, str]:
    username_ok = secrets.compare_digest(payload.username, settings.commodity_admin_username)
    password_ok = secrets.compare_digest(payload.password, settings.commodity_admin_password)
    if not username_ok or not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    request.session.clear()
    request.session.update({"authenticated": True, "username": payload.username})
    return {"username": payload.username}


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/auth/me")
def current_user(username: AdminUser) -> dict[str, str]:
    return {"username": username}


@app.get("/api/stats", response_model=DashboardStats)
def stats(_username: AdminUser) -> dict[str, int]:
    return database.dashboard_stats()


@app.get("/api/import/template")
def download_import_template(_username: AdminUser) -> StreamingResponse:
    filename = "商品资料批量导入模板.xlsx"
    return StreamingResponse(
        BytesIO(build_template()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.post("/api/import/excel")
async def import_excel(
    _username: AdminUser,
    file: Annotated[UploadFile, File(description="商品资料 Excel")],
) -> dict:
    filename = file.filename or ""
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="只支持 .xlsx 文件")
    content = await file.read(MAX_EXCEL_BYTES + 1)
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件为空")
    if len(content) > MAX_EXCEL_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件不能超过 5 MB")

    parsed = parse_catalog(content)
    imported_product_ids = {product.id for product in parsed.products}
    referenced_product_ids = {item.product_id for item in parsed.variants}
    external_product_ids = referenced_product_ids - imported_product_ids
    existing_ids = database.existing_product_ids(external_product_ids)
    missing_ids = external_product_ids - existing_ids
    if missing_ids:
        for item in parsed.variants:
            if item.product_id in missing_ids:
                parsed.errors.append(
                    ImportErrorItem(VARIANT_SHEET, item.row, f"关联商品 {item.product_id} 不存在")
                )
    if parsed.errors:
        return {
            "imported": False,
            "products_found": len(parsed.products),
            "variants_found": len(parsed.variants),
            "errors": [
                {"sheet": item.sheet, "row": item.row, "message": item.message}
                for item in parsed.errors[:100]
            ],
        }
    try:
        counts = database.bulk_upsert_catalog(
            parsed.products,
            [(item.product_id, item.variant) for item in parsed.variants],
        )
    except errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="内部商品编码与数据库中其他商品重复，请修改后重新导入",
        ) from exc
    return {"imported": True, **counts, "errors": []}


@app.get("/api/products", response_model=ProductListResponse)
def products(
    _username: AdminUser,
    search: str = Query(default="", max_length=200),
    product_status: str = Query(default="all", alias="status", pattern="^(all|draft|published|offline)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    return database.list_products(search, product_status, page, page_size)


@app.get("/api/products/{product_id}", response_model=ProductRead)
def product_detail(product_id: str, _username: AdminUser) -> dict:
    product = database.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return product


@app.post("/api/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, _username: AdminUser) -> dict:
    try:
        return database.create_product(payload)
    except errors.UniqueViolation as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="商品 ID 或内部编码已存在") from exc


@app.put("/api/products/{product_id}", response_model=ProductRead)
def update_product(product_id: str, payload: ProductUpdate, _username: AdminUser) -> dict:
    try:
        product = database.update_product(product_id, payload)
    except errors.UniqueViolation as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="内部商品编码已被使用") from exc
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return product


@app.delete("/api/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: str, _username: AdminUser) -> Response:
    if not database.delete_product(product_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/products/{product_id}/variants", response_model=list[VariantRead])
def variants(product_id: str, _username: AdminUser) -> list[dict]:
    if database.get_product(product_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return database.list_variants(product_id)


@app.post(
    "/api/products/{product_id}/variants",
    response_model=VariantRead,
    status_code=status.HTTP_201_CREATED,
)
def create_variant(product_id: str, payload: VariantCreate, _username: AdminUser) -> dict:
    if database.get_product(product_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    try:
        return database.create_variant(product_id, payload)
    except errors.UniqueViolation as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SKU ID 已存在") from exc


@app.put("/api/variants/{sku_id}", response_model=VariantRead)
def update_variant(sku_id: str, payload: VariantUpdate, _username: AdminUser) -> dict:
    variant = database.update_variant(sku_id, payload)
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SKU 不存在")
    return variant


@app.delete("/api/variants/{sku_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(sku_id: str, _username: AdminUser) -> Response:
    if not database.delete_variant(sku_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SKU 不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/products")
def customer_service_product_search(
    query: str = Query(min_length=2, max_length=200),
    limit: int = Query(default=5, ge=1, le=5),
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, list[dict]]:
    expected = f"Bearer {settings.product_api_bearer_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的访问令牌")
    return {"data": database.search_published_profiles(query, limit)}


@app.get("/products/{product_id}")
def customer_service_product(
    product_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, dict]:
    expected = f"Bearer {settings.product_api_bearer_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的访问令牌")
    profile = database.get_published_profile(product_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在或尚未发布")
    return {"data": profile}
