from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from config import settings
from routers import plans, strava, workouts, auth, garmin as garmin_router, admin

Base.metadata.create_all(bind=engine)

from sqlalchemy import text
with engine.connect() as conn:
    for stmt in [
        "ALTER TABLE planned_workouts ADD COLUMN steps JSON",
        "ALTER TABLE strava_tokens ADD COLUMN user_id INTEGER REFERENCES users(id)",
        "ALTER TABLE training_plans ADD COLUMN ai_model TEXT DEFAULT 'claude-sonnet-4-6'",
        "ALTER TABLE training_plans ADD COLUMN plan_type TEXT DEFAULT 'race'",
        "ALTER TABLE training_plans ADD COLUMN plan_duration_weeks INTEGER",
        "ALTER TABLE users ADD COLUMN max_hr INTEGER",
    ]:
        try:
            conn.execute(text(stmt))
            conn.commit()
        except Exception:
            pass  # column already exists — safe to ignore

app = FastAPI(title="Run Trainer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
