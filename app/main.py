from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.database import init_db
from app.api import scenic, guide, globe


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="旅游查询与建议系统后端 API",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scenic.router)
app.include_router(guide.router)
app.include_router(globe.router)


@app.get("/")
def root():
    return {"message": "旅途智览 API 服务运行中", "version": "1.0.0"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
