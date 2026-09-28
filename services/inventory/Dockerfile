# Imagem do inventory-service (worker Celery).
# Contexto de build: raiz do repositório. Imagem base provisória (pin em M8-T03).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

COPY services/inventory/requirements.txt services/inventory/requirements.txt
RUN pip install --no-cache-dir -r services/inventory/requirements.txt

COPY shared shared
COPY services/__init__.py services/__init__.py
COPY services/inventory services/inventory
