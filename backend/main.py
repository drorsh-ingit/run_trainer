import logging
import traceback
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from database import Base, engine
from config import settings
from routers import plans, strava, workouts, auth, garmin as garmin_router, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("run_trainer")

Base.metadata.create_all(bind=engine)

from sqlalchemy import text
_migrations = [
    "ALTER TABLE planned_workouts ADD COLUMN IF NOT EXISTS steps JSON",
    "ALTER TABLE strava_tokens ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)",
    "ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS ai_model TEXT DEFAULT 'claude-sonnet-4-6'",
    "ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS plan_type TEXT DEFAULT 'race'",
    "ALTER TABLE training_plans ADD COLUMN IF NOT EXISTS plan_duration_weeks INTEGER",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS max_hr INTEGER",
    "ALTER TABLE workout_activities ADD COLUMN IF NOT EXISTS match_score INTEGER",
    "ALTER TABLE workout_activities ADD COLUMN IF NOT EXISTS match_comment TEXT",
    "ALTER TABLE workout_activities ADD COLUMN total_elevation_gain FLOAT",
    "ALTER TABLE planned_workouts ADD COLUMN distance_label TEXT",
]
for stmt in _migrations:
    try:
        with engine.connect() as conn:
            conn.execute(text(stmt))
            conn.commit()
    except Exception:
        pass  # column already exists or syntax not supported

app = FastAPI(title="Run Trainer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate"],
)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s %s: %s\n%s", request.method, request.url.path, exc, traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

app.include_router(auth.router)
app.include_router(plans.router)
app.include_router(strava.router)
app.include_router(workouts.router)
app.include_router(garmin_router.router)
app.include_router(garmin_router.plans_router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/debug-env")
def debug_env():
    import os
    return {
        "strava_client_id_env": os.environ.get("STRAVA_CLIENT_ID", "NOT_SET"),
        "strava_client_id_settings": settings.strava_client_id,
        "frontend_url": settings.frontend_url,
    }
