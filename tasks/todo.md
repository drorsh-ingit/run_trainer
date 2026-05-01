# Manual Activity-to-Workout Matching

## Tasks
- [x] Add `MatchCandidateOut` and `ManualMatchRequest` schemas to `backend/schemas.py`
- [x] Add `GET /plans/{plan_id}/match-candidates` endpoint to `backend/routers/garmin.py`
- [x] Add `POST /plans/{plan_id}/manual-match` endpoint to `backend/routers/garmin.py`
- [x] Add `MatchModal` component to `frontend/app/calendar/page.tsx`
- [x] Add "match"/"re-match" buttons to tooltips and wire up state in CalendarPage
- [x] Verify: frontend builds clean, backend imports/routes correct, match-candidates API returns data
- [ ] Full E2E browser test (requires synced activities)
