from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from config import settings
from routers import plans, strava, workouts

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Run Trainer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(plans.router)
app.include_router(strava.router)
app.include_router(workouts.router)


@app.get("/health")
def health():
    return {"status": "ok"}
