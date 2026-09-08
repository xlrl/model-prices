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


# --- canonical matching and comparison-table logic ---------------------------

from generate_price_chart import (
    build_compare_rows,
    canonical_display,
    canonical_key,
)


class TestCanonicalKey:
    def test_drops_vendor_prefix_region_and_variant(self):
        # Real ids from models.json: OpenRouter vs Requesty naming for the same
        # model all collapse to the same bare key.
        assert canonical_key("moonshotai/kimi-k2.5") == "kimi-k2.5"
        assert canonical_key("moonshot/kimi-k2.5") == "kimi-k2.5"
        assert canonical_key("deepinfra/moonshotai/Kimi-K2.5") == "kimi-k2.5"
        assert canonical_key("bedrock/kimi-k2.5@us-east-1") == "kimi-k2.5"

    def test_strips_batch_and_free_suffixes(self):
        assert canonical_key("anthropic/claude-sonnet-5:batch") == "claude-sonnet-5"
        assert canonical_key("cohere/north-mini-code:free") == "north-mini-code"
        assert canonical_key("openai/gpt-5.6-terra:batch") == "gpt-5.6-terra"

    def test_strips_leading_tilde(self):
        # OpenRouter marks proxied/latest variants with a leading ~.
        assert canonical_key("~moonshotai/kimi-latest") == "kimi-latest"

    def test_deeply_nested_requesty_id_keeps_only_last_segment(self):
        # Requesty sometimes nests the lab under a backend: backend/lab/model.
        assert canonical_key("deepinfra/moonshotai/Kimi-K2.6:flex") == "kimi-k2.6"

    def test_plain_id_without_slash(self):
        assert canonical_key("gpt-4o-2024-05-13") == "gpt-4o-2024-05-13"


class TestCanonicalDisplay:
    def test_keeps_one_slash_for_context(self):
        assert canonical_display("moonshotai/kimi-k2.5") == "moonshotai/kimi-k2.5"
        assert canonical_display("anthropic/claude-sonnet-5") == "anthropic/claude-sonnet-5"

    def test_takes_last_two_segments_from_nested_id(self):
        # deepinfra/moonshotai/Kimi-K2.5 -> moonshotai/kimi-k2.5
        assert canonical_display("deepinfra/moonshotai/Kimi-K2.5") == "moonshotai/kimi-k2.5"

    def test_strips_region_and_variant(self):
        assert canonical_display("bedrock/kimi-k2.5@us-east-1") == "bedrock/kimi-k2.5"
        assert canonical_display("anthropic/claude-sonnet-5:batch") == "anthropic/claude-sonnet-5"

    def test_strips_leading_tilde(self):
        assert canonical_display("~moonshotai/kimi-latest") == "moonshotai/kimi-latest"

    def test_plain_id_without_slash(self):
        assert canonical_display("gpt-4o-2024-05-13") == "gpt-4o-2024-05-13"


def _cmp_row(timestamp: str, provider: str, model_id: str, inp: float, out: float) -> dict:
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


class TestBuildCompareRows:
    def test_matches_across_different_vendor_prefixes(self):
        # OpenRouter calls it moonshotai/kimi-k2.5; Requesty routes it via
        # moonshot/, bedrock/@region, and deepinfra/moonshotai/. They should
        # join on the bare "kimi-k2.5" key.
        df = pd.DataFrame(
            [
                _cmp_row("2026-01-01T00:00:00", "openrouter", "moonshotai/kimi-k2.5", 0.45, 2.25),
                _cmp_row("2026-01-01T00:00:00", "requesty", "moonshot/kimi-k2.5", 0.40, 2.00),
                _cmp_row("2026-01-01T00:00:00", "requesty", "bedrock/kimi-k2.5@us-east-1", 0.50, 2.50),
                _cmp_row("2026-01-01T00:00:00", "requesty", "deepinfra/moonshotai/Kimi-K2.5", 0.30, 1.80),
            ]
        )
        rows = build_compare_rows(df)
        assert len(rows) == 1
        r = rows[0]
        assert r["model"] == "moonshotai/kimi-k2.5"
        # Requesty cheapest route wins (deepinfra @ 0.30).
        assert r["rq_id"] == "deepinfra/moonshotai/Kimi-K2.5"
        assert r["rq_in"] == 0.30
        assert r["rq_out"] == 1.80
        assert r["or_in"] == 0.45
        assert r["or_out"] == 2.25
        assert r["rq_routes"] == 3
        # delta = (rq - or) / or * 100 = (0.30 - 0.45) / 0.45 * 100 = -33.33...
        assert round(r["delta_in"], 1) == -33.3

    def test_openrouter_prefers_standard_variant_over_batch(self):
        # When OpenRouter has both gpt-5.6-terra and gpt-5.6-terra:batch, the
        # standard (non-suffixed) variant is used for the comparison.
        df = pd.DataFrame(
            [
                _cmp_row("2026-01-01T00:00:00", "openrouter", "openai/gpt-5.6-terra", 2.0, 10.0),
                _cmp_row("2026-01-01T00:00:00", "openrouter", "openai/gpt-5.6-terra:batch", 1.5, 9.0),
                _cmp_row("2026-01-01T00:00:00", "requesty", "openai/gpt-5.6-terra", 2.0, 10.0),
            ]
        )
        rows = build_compare_rows(df)
        assert len(rows) == 1
        r = rows[0]
        assert r["or_id"] == "openai/gpt-5.6-terra"
        assert r["or_in"] == 2.0
        assert r["or_out"] == 10.0
        assert r["delta_in"] == 0.0

    def test_uses_latest_snapshot_per_provider(self):
        df = pd.DataFrame(
            [
                _cmp_row("2026-01-01T00:00:00", "openrouter", "openai/gpt-4o", 5.0, 15.0),
                _cmp_row("2026-01-02T00:00:00", "openrouter", "openai/gpt-4o", 4.0, 12.0),
                _cmp_row("2026-01-01T00:00:00", "requesty", "openai/gpt-4o", 2.5, 10.0),
            ]
        )
        rows = build_compare_rows(df)
        assert len(rows) == 1
        r = rows[0]
        # OpenRouter's latest (t2) price is 4.0 / 12.0.
        assert r["or_in"] == 4.0
        assert r["or_out"] == 12.0
        assert r["rq_in"] == 2.5
        assert round(r["delta_in"], 1) == -37.5

    def test_models_only_on_one_provider_are_excluded(self):
        df = pd.DataFrame(
            [
                _cmp_row("2026-01-01T00:00:00", "openrouter", "openrouter-only/model-x", 1.0, 2.0),
                _cmp_row("2026-01-01T00:00:00", "requesty", "requesty-only/model-y", 1.0, 2.0),
                _cmp_row("2026-01-01T00:00:00", "openrouter", "shared/m", 1.0, 2.0),
                _cmp_row("2026-01-01T00:00:00", "requesty", "shared/m", 1.0, 2.0),
            ]
        )
        rows = build_compare_rows(df)
        assert {r["model"] for r in rows} == {"shared/m"}

    def test_rows_sorted_by_openrouter_input_cost_descending(self):
        df = pd.DataFrame(
            [
                _cmp_row("2026-01-01T00:00:00", "openrouter", "a/cheap", 0.1, 0.2),
                _cmp_row("2026-01-01T00:00:00", "requesty", "a/cheap", 0.1, 0.2),
                _cmp_row("2026-01-01T00:00:00", "openrouter", "b/pricy", 10.0, 20.0),
                _cmp_row("2026-01-01T00:00:00", "requesty", "b/pricy", 10.0, 20.0),
                _cmp_row("2026-01-01T00:00:00", "openrouter", "c/mid", 1.0, 2.0),
                _cmp_row("2026-01-01T00:00:00", "requesty", "c/mid", 1.0, 2.0),
            ]
        )
        rows = build_compare_rows(df)
        assert [r["model"] for r in rows] == ["b/pricy", "c/mid", "a/cheap"]

    def test_zero_delta_is_neutral(self):
        # Identical prices -> delta 0.0, exercised by the renderer's < 0.01 guard.
        df = pd.DataFrame(
            [
                _cmp_row("2026-01-01T00:00:00", "openrouter", "anthropic/claude-sonnet-5", 2.0, 10.0),
                _cmp_row("2026-01-01T00:00:00", "requesty", "anthropic/claude-sonnet-5", 2.0, 10.0),
            ]
        )
        rows = build_compare_rows(df)
        assert rows[0]["delta_in"] == 0.0
        assert rows[0]["delta_out"] == 0.0
