FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.24

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-group dev --no-install-project

COPY . .
RUN uv sync --frozen --no-group dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
