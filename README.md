# Run Trainer

AI-powered running training plan app. Generates personalized plans via Claude, syncs with Strava, and adapts based on your performance and feedback.

## Stack

- **Frontend**: Next.js + Tailwind CSS + shadcn/ui → Vercel
- **Backend**: FastAPI (Python) + PostgreSQL → Railway
- **AI**: Claude API (plan generation & adjustments)
- **Integrations**: Strava (activity sync)

## Local Development

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env  # fill in your values
uvicorn main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Environment Variables

See `.env.example` for required variables.
