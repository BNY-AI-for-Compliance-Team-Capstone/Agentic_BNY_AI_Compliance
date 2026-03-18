from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from redis import Redis
from sqlalchemy import text
import requests

from backend.api.routes import router
from backend.config.settings import settings
from backend.knowledge_base.supabase_client import SupabaseClient

app = FastAPI(
    title="AI Compliance Reporting System",
    description="Multi-agent system for automated compliance report generation",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


def check_database_connection() -> bool:
    try:
        db = SupabaseClient()
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_weaviate_connection(timeout_seconds: int = 2) -> bool:
    try:
        url = settings.WEAVIATE_URL.rstrip("/") + "/v1/meta"
        headers = {}
        if settings.WEAVIATE_API_KEY:
            headers["Authorization"] = f"Bearer {settings.WEAVIATE_API_KEY}"
        response = requests.get(url, headers=headers, timeout=timeout_seconds)
        return response.status_code == 200
    except Exception:
        return False


def check_redis_connection(timeout_seconds: int = 2) -> bool:
    try:
        redis = Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        )
        return bool(redis.ping())
    except Exception:
        return False


def _run_with_timeout(check_fn, timeout_seconds: int) -> bool:
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(check_fn)
            return bool(future.result(timeout=timeout_seconds))
    except FuturesTimeoutError:
        return False
    except Exception:
        return False


def _build_health_payload(database_ok: bool, weaviate_ok: bool, redis_ok: bool) -> dict:
    services = {
        "database": database_ok,
        "weaviate": weaviate_ok,
        "redis": redis_ok,
    }
    all_ok = all(services.values())
    return {
        "status": "healthy" if all_ok else "degraded",
        "services": services,
    }


@app.get("/health")
async def health_check(full: bool = True) -> dict:
    if not full:
        return {
            "status": "healthy",
            "services": {
                "database": "skipped",
                "weaviate": "skipped",
                "redis": "skipped",
            },
        }

    return _build_health_payload(
        database_ok=_run_with_timeout(check_database_connection, timeout_seconds=4),
        weaviate_ok=_run_with_timeout(lambda: check_weaviate_connection(timeout_seconds=2), timeout_seconds=3),
        redis_ok=_run_with_timeout(lambda: check_redis_connection(timeout_seconds=2), timeout_seconds=3),
    )


@app.get("/health/lite")
async def health_check_lite() -> dict:
    return {
        "status": "healthy",
        "services": {
            "database": "skipped",
            "weaviate": "skipped",
            "redis": "skipped",
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
