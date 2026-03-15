# Run Trainer

AI-powered running training plan generator and coach. Uses Claude AI to create personalized marathon/long-distance training plans with an interactive conversational coaching interface.

## Tech Stack

**Frontend:** Next.js 16, React 19, TypeScript, Tailwind CSS 4
**Backend:** FastAPI, SQLAlchemy 2, Alembic, Anthropic SDK, Python-jose/passlib (JWT auth), Stravalib
**Database:** SQLite (default), configurable for PostgreSQL

## Project Structure

```
backend/
  main.py           # FastAPI app entry point
  config.py         # Pydantic settings (loads from .env)
  database.py       # SQLAlchemy setup
  schemas.py        # Pydantic request/response models
  models/models.py  # ORM models: User, TrainingPlan, PlannedWorkout, WorkoutFeedback, StravaToken
  routers/
    auth.py         # Auth endpoints
    plans.py        # Training plan CRUD & coaching
    strava.py       # Strava OAuth
    workouts.py     # Workout tracking
  services/
    auth.py         # JWT handling, user dependency
    claude.py       # Claude API integration (plan gen, coaching chat)
  seed_users.py     # DB seeding script

frontend/app/
  page.tsx          # Home
  layout.tsx        # Root layout
  components/       # Nav, GeneratingProgress
  hooks/useAuth.ts  # JWT auth hook + apiFetch helper
  login/            # Login page
  register/         # Register page
  dashboard/        # User dashboard
  plans/new/        # Create plan form
  plans/[id]/       # View/edit plan + coaching chat
```

## Dev Commands

**Backend:**
```bash
cd backend
source .venv/bin/activate
python main.py          # API at http://localhost:8000
python seed_users.py    # Seed test data
```

**Frontend:**
```bash
cd frontend
npm run dev   # http://localhost:3000
npm run build
npm run lint
```

**Useful URLs:**
- Frontend: http://localhost:3000
- API: http://localhost:8000
- Swagger docs: http://localhost:8000/docs
- Health check: GET http://localhost:8000/health

## Environment Variables

Backend reads from `backend/.env`:
- `ANTHROPIC_API_KEY` — required for Claude AI features
- `SECRET_KEY` — JWT signing key
- `DATABASE_URL` — defaults to SQLite
- `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` — Strava OAuth
- `FRONTEND_URL` — for CORS config

## Key Patterns

- **Auth:** JWT via `services/auth.py`; use `get_current_user` dependency in protected routes
- **Claude integration:** All AI calls go through `services/claude.py`
- **DB tables:** Auto-created on startup via `Base.metadata.create_all()` (no migrations run yet)
- **API fetch (frontend):** Use `apiFetch` from `hooks/useAuth.ts` for authenticated requests

## No Tests Yet

No test suite exists. When adding tests: use pytest for backend, Jest/Vitest for frontend.
