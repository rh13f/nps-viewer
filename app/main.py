from fastapi import FastAPI
from app.routers import health, sessions, failures, reason_codes, mac, live, aps

app = FastAPI(title="NPS Viewer API", version="1.0.0")

app.include_router(health.router)
app.include_router(sessions.router)
app.include_router(failures.router)
app.include_router(reason_codes.router)
app.include_router(mac.router)
app.include_router(live.router)
app.include_router(aps.router)
