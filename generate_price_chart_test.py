"""Tests for generate_price_chart.feed change detection and rendering."""

import pandas as pd

from generate_price_chart import find_price_changes, render_rss


def _row(timestamp: str, provider: str, model_id: str, inp: float, out: float) -> dict:
    return {
        "timestamp": timestamp,
        "provider": provider,
        "model_id": model_id,
        "model_name": model_id,
        "input_cost": inp,
        "output_cost": out,
        "cache_read_cost": 0.0,
        "cache_write_cost": 0.0,
    }


def test_price_change_emitted_for_kept_model():
    df = pd.DataFrame(
        [
            _row("2026-01-01T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "a/one", 1.5, 2.0),
        ]
    )
    changes = find_price_changes(df)
    assert changes == {
        "2026-01-02T00:00:00": [
            "openrouter/a/one: input $1 → $1.5, output $2 → $2 (per Mtok)"
        ]
    }


def test_added_emitted_for_new_model():
    df = pd.DataFrame(
        [
            _row("2026-01-01T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "a/two", 3.0, 4.0),
        ]
    )
    changes = find_price_changes(df)
    assert changes == {
        "2026-01-02T00:00:00": ["+ openrouter/a/two: added — input $3, output $4 (per Mtok)"]
    }


def test_removed_emitted_for_dropped_model():
    df = pd.DataFrame(
        [
            _row("2026-01-01T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-01T00:00:00", "openrouter", "a/two", 3.0, 4.0),
            _row("2026-01-02T00:00:00", "openrouter", "a/one", 1.0, 2.0),
        ]
    )
    changes = find_price_changes(df)
    assert changes == {
        "2026-01-02T00:00:00": ["- openrouter/a/two: removed — was input $3, output $4 (per Mtok)"]
    }


def test_first_snapshot_is_silent():
    # No prior snapshot to compare against -> the initial list is a baseline,
    # not a wave of "added" events.
    df = pd.DataFrame([_row("2026-01-01T00:00:00", "openrouter", "a/one", 1.0, 2.0)])
    assert find_price_changes(df) == {}


def test_readd_split_into_removed_then_added():
    # a/one is present at t1, absent at t2, back at t3. The gap must not collapse
    # into a single spanning price-change line; it should be removed@t2 + added@t3.
    df = pd.DataFrame(
        [
            _row("2026-01-01T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "b/three", 3.0, 4.0),
            _row("2026-01-03T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-03T00:00:00", "openrouter", "b/three", 3.0, 4.0),
        ]
    )
    changes = find_price_changes(df)
    assert changes["2026-01-02T00:00:00"] == [
        "+ openrouter/b/three: added — input $3, output $4 (per Mtok)",
        "- openrouter/a/one: removed — was input $1, output $2 (per Mtok)",
    ]
    assert changes["2026-01-03T00:00:00"] == [
        "+ openrouter/a/one: added — input $1, output $2 (per Mtok)"
    ]


def test_per_provider_sparsity_does_not_imply_removal():
    # openrouter is recorded at t1 and t3 (unchanged in between); eurouter is
    # recorded at t2. openrouter/a/one is absent from the t2 row only because
    # openrouter was not re-recorded there, not because it was removed.
    df = pd.DataFrame(
        [
            _row("2026-01-01T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "eurouter", "b/three", 3.0, 4.0),
            _row("2026-01-03T00:00:00", "openrouter", "a/one", 1.0, 2.0),
        ]
    )
    assert find_price_changes(df) == {}


def test_lines_sorted_within_entry():
    df = pd.DataFrame(
        [
            _row("2026-01-01T00:00:00", "openrouter", "z/old", 1.0, 2.0),
            _row("2026-01-01T00:00:00", "openrouter", "a/old", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "z/old", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "a/old", 1.5, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "m/new", 5.0, 6.0),
        ]
    )
    lines = find_price_changes(df)["2026-01-02T00:00:00"]
    assert lines == sorted(lines)


def test_render_rss_includes_added_removed_and_model_changes_title():
    df = pd.DataFrame(
        [
            _row("2026-01-01T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "a/one", 1.0, 2.0),
            _row("2026-01-02T00:00:00", "openrouter", "a/two", 3.0, 4.0),
        ]
    )
    rss = render_rss(df)
    assert "<title>Model changes – 2026-01-02 (1 model)</title>" in rss
    assert "+ openrouter/a/two: added — input $3, output $4 (per Mtok)" in rss
