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

## UX: Match progress feedback to the actual phase of work
**Context:** The progress bar (with step-by-step labels) ran during every SSE call — including quick Q&A exchanges where it was misleading and unnecessary. First fix (tying to initial call only) was wrong — the progress should show during the *final* plan generation phase, not the initial one.
**Rule:** When an AI flow has both fast (Q&A) and slow (generation) phases, split them into separate API calls. Show lightweight indicators (bouncing dots) for fast phases and progress bars for the slow phase. Let the user explicitly trigger the slow phase with a button so it's clear when the long wait starts. Follow the coaching flow pattern: Q&A prompt → ready signal → build prompt.

## Data: Include ALL relevant data in AI prompts
**Context:** Unmatched Strava activities (runs that didn't match any planned workout) were not included in the assessment prompt. The coach only saw matched workouts, missing significant training volume.
**Rule:** When building context for plan assessment, query and include unmatched activities (`WorkoutActivity` with `workout_id=NULL`) so the AI has the full picture of actual training load.

## CSS: Unlayered rules beat Tailwind v4 utilities — and verify UI fixes in the triggering condition
**Context:** Input text stayed washed-out on Chrome/Safari iOS after two attempted fixes. Root cause was twofold: (1) a bare `input,textarea,select { color: inherit }` rule in globals.css is *unlayered*, and in Tailwind v4 unlayered CSS overrides the `utilities` layer regardless of specificity — so it silently defeated every `text-gray-900` on inputs; (2) a leftover `@media (prefers-color-scheme: dark)` block flipped `--foreground` to near-white even though the app has zero `dark:` variants and is light-only. On a dark-mode phone the inputs inherited near-white and `-webkit-text-fill-color` painted it on white fields. I "fixed" it twice without reproducing the actual condition (dark-mode + mobile), so it kept not working for the user.
**Rule:** (a) In Tailwind v4, never write unlayered element rules that set properties Tailwind utilities also set (`color`, `background`, etc.) — they win over utilities and break them app-wide. If you must, wrap in `@layer base`. (b) For a light-only app (no `dark:` variants), pin `color-scheme: light` and delete leftover `prefers-color-scheme: dark` blocks — iOS applies its own dark form-control rendering otherwise. (c) Reproduce the *exact* failing condition before claiming a UI fix: for "looks wrong on my phone in dark mode," verify in a mobile viewport with dark color scheme (browser `resize_window` colorScheme:dark + mobile preset) and confirm computed `color`/`-webkit-text-fill-color`, not just eyeball a desktop light-mode render.

## AI/Backend: Never swallow AI-batch failures silently; size token budgets from real output
**Context:** Pushing a plan to intervals.icu reported "Done — pushed 0 workout(s)" with no error. Root cause: `generate_steps_for_workouts` set `max_tokens = workouts*400 + 1000` = 5000 for a 10-workout batch, but detailed interval/tempo workouts emit ~700 output tokens of steps JSON each. The response hit `stop_reason: max_tokens`, the truncated JSON failed to parse, and `_ensure_steps` caught it with `except Exception: pass` — leaving every workout with `steps=None`, so all were skipped. The user saw a silent zero-push with no diagnosis.
**Rule:** (a) Size `max_tokens` from *measured* output size, not a guessed constant — verify by running a realistic (verbose) batch and checking `stop_reason` is `end_turn`, not `max_tokens`. Budget with headroom (used 900/workout for ~700 actual). (b) Keep AI batches small enough that `batch_size * per_item_budget` stays under the model cap; a single truncation loses the whole batch, so smaller batches also localize failure. (c) Never `except Exception: pass` around an AI call — at minimum `logger.exception(...)`, and surface a real error to the user when the operation can't complete. A user-facing "did nothing" with no reason is a bug, not a graceful degrade. (d) Detect `stop_reason == "max_tokens"` explicitly and raise a clear error rather than letting it surface as a downstream JSON parse failure.
