# Gateway ISO 20022 para Google Cloud Run. Contexto de build: la raíz del repositorio.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /srv
RUN pip install --no-cache-dir "fastapi>=0.115,<1.0" "uvicorn>=0.32,<1.0" "lxml>=5.3,<7.0"

# Solo el adaptador ISO 20022 (mensajes y XSD); nada de bases, secretos ni dominios.
COPY app/__init__.py app/__init__.py
COPY app/domains/__init__.py app/domains/__init__.py
COPY app/domains/iso20022/__init__.py app/domains/iso20022/messages.py app/domains/iso20022/
COPY app/domains/iso20022/schemas app/domains/iso20022/schemas
COPY services/iso20022_gateway/main.py gateway_main.py

RUN useradd --create-home gateway
USER gateway

CMD ["sh", "-c", "uvicorn gateway_main:app --host 0.0.0.0 --port ${PORT}"]
