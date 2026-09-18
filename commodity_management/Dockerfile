FROM node:22-alpine AS frontend-assets

WORKDIR /assets
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/commodity-management

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=frontend-assets /assets/node_modules/lucide/dist/umd/lucide.js \
    /srv/commodity-management/app/static/vendor/lucide.js

RUN useradd --create-home --uid 10001 commodity
USER commodity

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
