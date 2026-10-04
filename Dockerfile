FROM python:3.13-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/lariasca1994/QALabSPBVI" \
      org.opencontainers.image.description="API de QALabSPBVI (FastAPI)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Controlador ODBC 18 de Microsoft: lo requiere pyodbc para conectar DIFE (SQL Server / Azure SQL).
# Oracle (DICE) usa python-oracledb en modo thin y no necesita cliente instalado.
# libgssapi-krb5-2 llega con curl; sin instalarla explícitamente, autoremove la borra y el
# driver falla en tiempo de ejecución ("Can't open lib"). El build falla si falta alguna.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 libgssapi-krb5-2 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && ! ldd /opt/microsoft/msodbcsql18/lib64/libmsodbcsql-18.*.so.* | grep "not found"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir ".[postgres]"

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
