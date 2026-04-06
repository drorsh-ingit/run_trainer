# Plan Assessment Feature

## Tasks
- [x] Add assessment schemas to backend (AssessStartRequest, AssessReplyRequest, AssessApplyRequest)
- [x] Add ASSESS_SYSTEM_PROMPT, _build_comparison_context(), assess_plan_revision() to claude.py
- [x] Add /assess/start, /assess/reply, /assess/apply endpoints to plans.py
- [x] Add assessment UI panel with chat, preview, save/dismiss to frontend
- [x] Replace standalone button with "Adjust Plan" dropdown (Re-assess Plan / Adjust Plan)
- [x] Fix model selector to appear before assessment starts (open panel first, then "Start Assessment" button)
- [x] Add GeneratingProgress component with "assess" mode for long operations
- [x] Fix scroll behavior — stay on assessment panel, don't jump away
- [x] Include today's completed workouts in comparison context
- [x] Include unmatched activities in assessment prompt
- [x] Gracefully handle non-JSON Claude responses (treat as conversation message)
- [x] Fix pre-existing SQLite migration crash (wrap in try/except)

## Review
All tasks completed and pushed across 3 commits:
1. `ce39d03` — Core feature: backend + frontend assessment flow
2. `a7b010a` — UX improvements: dropdown, progress bar, error handling
3. `66f5c50` — Data completeness: today's workouts + unmatched activities
