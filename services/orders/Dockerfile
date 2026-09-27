# Imagem do orders-service (API FastAPI e worker Celery usam a mesma imagem).
# Contexto de build: raiz do repositório. Imagem base provisória (pin em M8-T03).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

COPY services/orders/requirements.txt services/orders/requirements.txt
RUN pip install --no-cache-dir -r services/orders/requirements.txt

COPY shared shared
COPY services/__init__.py services/__init__.py
COPY services/orders services/orders
