"""Unit tests for the demo risk-history storyline (UI task U7). Pure logic only: the DB/model parts
are exercised by running the script against the demo stack."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from scripts.demo_history import (
    ATTENTION_SCORE,
    history_timestamps,
    pick_nearest,
    storyline_targets,
)


def test_a_machine_needing_attention_gets_a_rising_path_that_stays_below_its_latest_score() -> None:
    targets = storyline_targets(90.0, 6, random.Random(1))
    assert len(targets) == 6
    assert targets == sorted(targets)  # worsening, never improving
    assert targets[0] < 15  # starts low
    assert targets[-1] < 90.0  # the real latest score is the peak, not duplicated
    assert targets[-1] > 40  # but it does get close


@pytest.mark.parametrize("final", [0.0, 3.0, 20.0, 33.9])
def test_a_steady_machine_wobbles_around_its_own_latest_score(final: float) -> None:
    targets = storyline_targets(final, 8, random.Random(1))
    assert len(targets) == 8
    assert all(0.0 <= t <= 100.0 for t in targets)
    assert all(abs(t - final) <= 1.5 for t in targets)


def test_the_attention_threshold_is_the_apps_medium_band_start() -> None:
    assert ATTENTION_SCORE == 34.0
    just_under = storyline_targets(33.9, 4, random.Random(1))
    assert all(abs(t - 33.9) <= 1.5 for t in just_under)  # steady, not a ramp
    just_over = storyline_targets(34.0, 4, random.Random(1))
    assert just_over == sorted(just_over) and just_over[0] < 10  # a ramp


def test_storylines_are_deterministic_for_a_seed_and_differ_across_seeds() -> None:
    assert storyline_targets(5.0, 5, random.Random(7)) == storyline_targets(
        5.0, 5, random.Random(7)
    )
    assert storyline_targets(5.0, 5, random.Random(7)) != storyline_targets(
        5.0, 5, random.Random(8)
    )


def test_zero_or_negative_steps_gives_no_targets() -> None:
    assert storyline_targets(90.0, 0, random.Random(1)) == []
    assert storyline_targets(90.0, -3, random.Random(1)) == []


def test_the_score_is_clamped_into_a_sane_range_even_for_extreme_inputs() -> None:
    assert all(0.0 <= t <= 1.5 for t in storyline_targets(0.0, 20, random.Random(3)))
    ramp = storyline_targets(100.0, 5, random.Random(3))
    assert all(0.0 <= t <= 100.0 for t in ramp)


def test_pick_nearest_picks_the_closest_record_for_each_target() -> None:
    pool = [1.0, 20.0, 45.0, 70.0, 95.0]
    assert pick_nearest(pool, [2.0, 44.0, 90.0]) == [0, 2, 4]


def test_pick_nearest_never_reuses_a_record_even_when_targets_repeat() -> None:
    picks = pick_nearest([1.0, 2.0, 50.0], [1.0, 1.0, 1.0])
    assert sorted(picks) == [0, 1, 2]


def test_pick_nearest_refuses_when_the_pool_is_too_small() -> None:
    with pytest.raises(ValueError, match="need 3 distinct records but the pool has 2"):
        pick_nearest([1.0, 2.0], [1.0, 2.0, 3.0])


def test_pick_nearest_with_no_targets_picks_nothing() -> None:
    assert pick_nearest([1.0, 2.0], []) == []


def test_history_timestamps_are_one_per_past_day_oldest_first_and_all_in_the_past() -> None:
    end = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    stamps = history_timestamps(end, 3)
    assert stamps == [end - timedelta(days=3), end - timedelta(days=2), end - timedelta(days=1)]
    assert all(s < end for s in stamps)
    assert history_timestamps(end, 0) == []
