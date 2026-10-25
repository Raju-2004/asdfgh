from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import STATIC_DIR
from backend.app.routes import router

app = FastAPI(
    title="Wind Forecast Monitor API",
    description="Day-ahead UK wind forecasts (Elexon WINDFOR) vs actuals (FUELHH WIND), January 2026 decisions.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(router)

# Serve the built React app when running as a single container (Docker).
# On Vercel the static files are served by the platform, so this is skipped.
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
