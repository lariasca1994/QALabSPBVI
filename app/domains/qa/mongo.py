from functools import lru_cache
from typing import Annotated

from fastapi import Depends
import httpx
from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import get_settings


@lru_cache
def get_mongo_client() -> MongoClient:
    settings = get_settings()
    return MongoClient(
        settings.mongodb_url,
        appname="QALabSPBVI",
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
        socketTimeoutMS=10000,
        retryWrites=True,
    )


@lru_cache
def get_qa_database() -> Database:
    settings = get_settings()
    database = get_mongo_client()[settings.mongodb_database]
    ensure_qa_indexes(database)
    return database


def ensure_qa_indexes(database: Database) -> None:
    database.epics.create_index("key", unique=True)
    database.epics.create_index("members.user_id")
    database.work_items.create_index("key", unique=True)
    database.work_items.create_index([("epic_key", 1), ("kind", 1)])
    database.issues.create_index("key", unique=True)
    database.issues.create_index("epic_key")
    database.executions.create_index("key", unique=True)
    # Lista de llaves de cada épica y contexto de identificadores generados por los CP.
    database.qa_keys.create_index(
        [("epic_key", 1), ("key_type", 1), ("key_value", 1), ("spbvi_id", 1)], unique=True
    )
    database.qa_keys.create_index([("epic_key", 1), ("created_at_epoch", -1)])
    database.qa_context.create_index([("epic_key", 1), ("kind", 1), ("name", 1)], unique=True)
    database.executions.create_index(
        [("case_key", 1), ("created_at_epoch", -1)]
    )


QaDatabase = Annotated[Database, Depends(get_qa_database)]


# 30 s: alcanza para que una base pausada (Azure SQL en australiaeast) se reanude en la
# primera solicitud; un CP no debe fallar solo por un arranque en frío.
EXECUTION_TIMEOUT_SECONDS = 30.0


@lru_cache
def _shared_http_client() -> httpx.Client:
    # Crear un cliente por ejecución cuesta cientos de ms (contexto TLS); se comparte uno.
    return httpx.Client(timeout=EXECUTION_TIMEOUT_SECONDS, follow_redirects=False)


def get_qa_http_client() -> httpx.Client:
    return _shared_http_client()


QaHttpClient = Annotated[httpx.Client, Depends(get_qa_http_client)]


def close_mongo_client() -> None:
    get_qa_database.cache_clear()
    if get_mongo_client.cache_info().currsize:
        get_mongo_client().close()
        get_mongo_client.cache_clear()
