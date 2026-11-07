from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import Base, engine
from app.routers import auth, shipments
from app.services.routing import RoutingError
from app.services.valhalla import ValhallaClient, ValhallaError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience. Switch to Alembic migrations before going to production.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.valhalla = ValhallaClient(settings.valhalla_url, settings.valhalla_costing)
    yield
    await app.state.valhalla.aclose()
    await engine.dispose()


app = FastAPI(title="Hitch API", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(shipments.router)


@app.exception_handler(ValhallaError)
async def valhalla_error(_: Request, exc: ValhallaError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(RoutingError)
async def routing_error(_: Request, exc: RoutingError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
