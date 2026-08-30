# Fix: "Adjust a plan" chat returns "Load failed"

## Root causes found
1. **Revision always fails validation (primary).** `chat_plan_revision._slim_plan()` strips
   `description` from each workout before sending the plan to the model. The model mirrors that
   structure and returns workouts WITHOUT `description`, but `ClaudePlanResponse` requires it →
   182 "description Field required" validation errors on every adjust. Reproduced via curl.
2. **Stream can drop → Safari shows "Load failed".** During the long Claude call the SSE stream
   sends nothing after "Thinking…", and the DB-save path (`_save_workouts`/`commit`) runs OUTSIDE
   the try/except. A dropped/idle connection or a save exception aborts the stream mid-flight;
   Safari surfaces that as the network error "Load failed".

## Plan
- [ ] Keep `description` in `_slim_plan` so the model preserves/edits real descriptions and
      produces schema-valid output.
- [ ] Add SSE heartbeat during the model call so long revisions don't idle-timeout the connection.
- [ ] Wrap the DB-save path in try/except and emit a clean `error` event instead of crashing the stream.
- [ ] Add backend tests (pytest): `_slim_plan` retains description; endpoint error paths.
- [ ] Verify a real adjust succeeds end-to-end via curl.

## Review

Three distinct, compounding bugs made "Adjust a plan" fail. All fixed and verified end-to-end
against a real running server (general plan #2, 12 weeks → succeeds and persists; race plan #1,
46 weeks → streams with heartbeats, no connection drop).

1. **Validation crash (race plans).** `chat_plan_revision` stripped `description` from each
   workout before showing the plan to the model; the model mirrored that and returned workouts
   with no `description`, failing `ClaudePlanResponse` (182 errors). Fix: keep `description` in
   `_slim_plan_for_revision` (extracted to module level for testing).

2. **500 on general plans.** The endpoint always built a `race`-type `PlanCreateRequest`, which
   requires `goal_distance_km`; general plans (and race plans past their date) 500'd before
   streaming. Fix: `_plan_to_context_request()` uses `model_construct` to rehydrate stored plans
   of any type without re-running create-time validators.

3. **Dropped stream → "Load failed".** During the long (30s–5min) model call the SSE stream sent
   nothing, and the DB save reused the request session (closed once the endpoint returns, before
   the generator streams). Fixes: run the model call on a worker thread and emit `: keepalive`
   heartbeats every 10s; do the save on a fresh `SessionLocal()` re-fetching the plan by id; wrap
   the save in try/except so failures become a clean `error` event instead of aborting the socket.

Tests: `backend/tests/test_plan_revision.py` (9 tests) — slim-plan retains description, context
rehydration for general/past-date plans. Full suite: 29 passed.

### Known limitation (not fixed — user chose "just the bug fix")
Adjusting a large plan regenerates the ENTIRE plan, so a 46-week plan takes ~3–5 min. Heartbeats
keep it from failing, but a future change could regenerate only affected weeks / stream incrementally.

## Follow-up fix (production Postgres) — FK violation on save
Surfaced only after the clean error event above exposed it (SQLite hid it — no FK enforcement).
Adjusting a plan deleted+recreated all planned_workouts, violating the FK from workout_activities
(and workout_feedback) → "Couldn't save the revised plan: ForeignKeyViolation ... Key (id)=(700)".

Fix: `_replace_plan_workouts` detaches linked activities/feedback, deletes+recreates the workouts,
then re-links each child to the new workout on the same date. No activity/feedback rows are
deleted (no data loss); children whose date no longer has a workout are left unmatched (a
supported state). Verified with `tests/test_plan_workout_replace.py` running SQLite with
foreign_keys=ON to mirror Postgres. Full suite: 33 passed.
