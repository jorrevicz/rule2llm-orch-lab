# Imagem do inventory-service (worker Celery).
# Contexto de build: raiz do repositório. Base fixada por digest (índice multiarquitetura, M8-T03):
# Python 3.12.16, Debian 13.7, pip 25.0.1. A tag é só legível; quem decide é o digest.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

COPY services/inventory/requirements.txt services/inventory/requirements.txt
RUN pip install --no-cache-dir -r services/inventory/requirements.txt

COPY shared shared
COPY services/__init__.py services/__init__.py
COPY services/inventory services/inventory
