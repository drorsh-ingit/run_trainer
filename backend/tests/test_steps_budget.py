"""Regression tests for the workout-steps token budget.

The intervals/garmin push silently produced "pushed 0 workout(s)" because the
steps batch's max_tokens (workouts*400 + 1000 = 5000 for 10 detailed workouts)
truncated the JSON, which _ensure_steps then swallowed. The budget must give
enough headroom (~700 output tokens/workout observed) for the batch sizes the
callers actually use (BATCH = 10).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.claude import _steps_max_tokens

# Observed: detailed interval workouts emit ~700 output tokens of steps each.
OBSERVED_TOKENS_PER_WORKOUT = 700
CALLER_BATCH_SIZE = 10  # garmin._ensure_steps / plans export


def test_budget_covers_a_full_batch_of_detailed_workouts():
    budget = _steps_max_tokens(CALLER_BATCH_SIZE)
    assert budget >= CALLER_BATCH_SIZE * OBSERVED_TOKENS_PER_WORKOUT


def test_old_formula_would_have_truncated():
    # The previous formula under-provisioned a 10-workout batch.
    old = min(10 * 400 + 1000, 16000)
    assert old < 10 * OBSERVED_TOKENS_PER_WORKOUT  # 5000 < 7000 -> truncation
    assert _steps_max_tokens(10) >= 10 * OBSERVED_TOKENS_PER_WORKOUT


def test_budget_scales_with_batch_size():
    assert _steps_max_tokens(5) < _steps_max_tokens(10)


def test_budget_never_exceeds_model_cap():
    # A full batch must not request a truncating/invalid budget.
    assert _steps_max_tokens(CALLER_BATCH_SIZE) <= 16000
    assert _steps_max_tokens(1000) == 16000


def test_batch_size_fits_under_cap_with_headroom():
    # The caller's batch size must fit within the cap at the observed rate,
    # otherwise batches truncate no matter the formula.
    assert CALLER_BATCH_SIZE * OBSERVED_TOKENS_PER_WORKOUT <= _steps_max_tokens(CALLER_BATCH_SIZE) <= 16000
