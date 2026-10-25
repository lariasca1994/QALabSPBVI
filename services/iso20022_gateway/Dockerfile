# Gateway ISO 20022 (Render, plan gratuito). Contexto de build: la raíz del repositorio.
FROM python:3.13.17-slim-bookworm
# Seguridad en cada compilación: actualizaciones del sistema (Linux), pip al día (sus
# CVE de octubre 2026) y almacén de certificados sin las raíces que firmaron los
# intermedios comprometidos de eMudhra (emSign y Comodo "AAA Certificate Services").
RUN if command -v apt-get > /dev/null; then \
        apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*; \
    fi \
    && if command -v pip > /dev/null; then pip install --no-cache-dir --upgrade pip > /dev/null; fi \
    && if command -v update-ca-certificates > /dev/null; then \
        rm -f /usr/share/ca-certificates/mozilla/emSign_* /usr/share/ca-certificates/mozilla/Comodo_AAA_Services_root.crt \
        && update-ca-certificates --fresh > /dev/null 2>&1; \
    fi

LABEL org.opencontainers.image.source="https://github.com/lariasca1994/QALabSPBVI" \
      org.opencontainers.image.description="Gateway ISO 20022 de laboratorio de QALabSPBVI"

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
