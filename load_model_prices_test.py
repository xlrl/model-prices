import json

from load_model_prices import flatten_snapshot, load_price_history, resolve_timestamp


def test_resolve_timestamp_prefers_meta_updated(tmp_path):
    path = tmp_path / "models_20260101_000000.json"
    path.write_text(json.dumps({"_meta": {"updated": "2026-01-02T03:04:05Z"}, "providers": {}}))
    data = json.loads(path.read_text())
    assert resolve_timestamp(data, str(path)) == "2026-01-02T03:04:05Z"


def test_resolve_timestamp_falls_back_to_mtime(tmp_path):
    path = tmp_path / "models_20260101_000000.json"
    path.write_text(json.dumps({"providers": {}}))
    data = json.loads(path.read_text())
    ts = resolve_timestamp(data, str(path))
    assert ts.endswith("Z")


def test_flatten_snapshot_one_row_per_model_across_providers():
    data = {
        "providers": {
            "openrouter": {
                "models": [
                    {"id": "a/one", "name": "One", "cost": {"input": 1.0, "output": 2.0}},
                    {"id": "a/two", "name": "Two", "cost": {"input": 0.5, "output": 1.5, "cacheRead": 0, "cacheWrite": 0}},
                ]
            },
            "eurouter": {
                "models": [
                    {"id": "b/three", "name": "Three", "cost": {"input": 3.0, "output": 4.0}},
                ]
            },
        }
    }
    rows = flatten_snapshot(data, "2026-01-01T00:00:00Z")
    assert len(rows) == 3
    ids = {(r["provider"], r["model_id"]) for r in rows}
    assert ids == {("openrouter", "a/one"), ("openrouter", "a/two"), ("eurouter", "b/three")}


def test_flatten_snapshot_normalizes_cache_costs_to_float():
    data = {
        "providers": {
            "openrouter": {
                "models": [
                    {"id": "a/one", "name": "One", "cost": {"input": 1.0, "output": 2.0, "cacheRead": 0, "cacheWrite": 0}},
                ]
            }
        }
    }
    rows = flatten_snapshot(data, "2026-01-01T00:00:00Z")
    assert isinstance(rows[0]["cache_read_cost"], float)
    assert isinstance(rows[0]["cache_write_cost"], float)


def test_load_price_history_reads_all_snapshots(tmp_path):
    for i, ts in enumerate(["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]):
        path = tmp_path / f"models_2026010{i + 1}_000000.json"
        path.write_text(
            json.dumps(
                {
                    "_meta": {"updated": ts},
                    "providers": {
                        "openrouter": {
                            "models": [{"id": "a/one", "name": "One", "cost": {"input": 1.0 + i, "output": 2.0}}]
                        }
                    },
                }
            )
        )
    rows = load_price_history(str(tmp_path))
    assert len(rows) == 2
    assert {r["timestamp"] for r in rows} == {"2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"}


def test_load_price_history_skips_unchanged_provider_data(tmp_path):
    for i, ts in enumerate(["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"]):
        path = tmp_path / f"models_2026010{i + 1}_000000.json"
        path.write_text(
            json.dumps(
                {
                    "_meta": {"updated": ts},
                    "providers": {
                        "openrouter": {"models": [{"id": "a/one", "name": "One", "cost": {"input": 1.0, "output": 2.0}}]},
                        "eurouter": {"models": [{"id": "b/three", "name": "Three", "cost": {"input": 3.0, "output": 4.0}}]},
                    },
                }
            )
        )
    rows = load_price_history(str(tmp_path))
    # neither provider's data changed between snapshots -> only the first data point is kept for each
    for provider in ("openrouter", "eurouter"):
        provider_rows = [r for r in rows if r["provider"] == provider]
        assert len(provider_rows) == 1
        assert provider_rows[0]["timestamp"] == "2026-01-01T00:00:00Z"
