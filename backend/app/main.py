from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .config import settings
from .jobs import generation_queue


def create_app() -> FastAPI:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    projects_dir = settings.data_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await generation_queue.start()
        yield
        await generation_queue.stop()

    app = FastAPI(title="AI Film Agent", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    app.mount(
        "/media", StaticFiles(directory=projects_dir), name="media"
    )

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
