# Lessons Learned

## UX: Don't auto-scroll the page when content updates inside a panel
**Context:** Assessment panel messages caused `scrollIntoView` on the whole page, pushing the user away from the panel they were looking at.
**Rule:** When updating content inside a scrollable sub-container, scroll within that container (`el.scrollTop = el.scrollHeight`), not using `scrollIntoView` which scrolls the entire page.

## UX: Let users configure options before triggering an action
**Context:** Clicking "Re-assess Plan" immediately fired the API call, but the panel also had a model selector — so users could only change the model *after* the assessment already started.
**Rule:** If an action panel has configuration options (model selector, parameters), open the panel first with a "Start" button. Don't auto-trigger the action on panel open.

## UX: Show progress feedback for long AI operations
**Context:** Plan assessment can take 30-60+ seconds when generating revised workouts. The UI just showed "Thinking..." with no progress indication, making users think it was stuck.
**Rule:** Reuse `GeneratingProgress` component (or similar step-by-step progress) for any AI operation that takes >10 seconds. The existing component already supports multiple modes — just add a new step list.

## Data: Include today's completed data in comparisons
**Context:** `_build_comparison_context` filtered `scheduled_date >= today`, excluding workouts done today even when they had synced activities. The coach then claimed the user hadn't done a run they'd actually completed.
**Rule:** When building historical comparison data, include today's entries if they have associated completion data (e.g., a synced activity). Use `scheduled_date > today or (scheduled_date == today and not completed)` as the exclusion filter.

## Data: Include ALL relevant data in AI prompts
**Context:** Unmatched Strava activities (runs that didn't match any planned workout) were not included in the assessment prompt. The coach only saw matched workouts, missing significant training volume.
**Rule:** When building context for plan assessment, query and include unmatched activities (`WorkoutActivity` with `workout_id=NULL`) so the AI has the full picture of actual training load.
