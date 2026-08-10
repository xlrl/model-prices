"""Flatten models_<timestamp>.json snapshots into a long-form price history CSV."""

import argparse
import glob
import json
import os
from datetime import UTC, datetime


def resolve_timestamp(data: dict, path: str) -> str:
    meta = data.get("_meta")
    if meta and meta.get("updated"):
        return meta["updated"]
    dt = datetime.fromtimestamp(os.path.getmtime(path), tz=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def flatten_snapshot(data: dict, timestamp: str) -> list[dict]:
    rows: list[dict] = []
    for provider, provider_data in data.get("providers", {}).items():
        for model in provider_data.get("models", []):
            cost = model.get("cost", {})
            rows.append(
                {
                    "timestamp": timestamp,
                    "provider": provider,
                    "model_id": model["id"],
                    "model_name": model.get("name", model["id"]),
                    "input_cost": float(cost.get("input", 0.0)),
                    "output_cost": float(cost.get("output", 0.0)),
                    "cache_read_cost": float(cost.get("cacheRead", 0.0)),
                    "cache_write_cost": float(cost.get("cacheWrite", 0.0)),
                }
            )
    return rows


def rename_untimestamped_snapshot(data_dir: str) -> None:
    path = os.path.join(data_dir, "models.json")
    if not os.path.exists(path):
        return
    with open(path) as f:
        data = json.load(f)
    timestamp = resolve_timestamp(data, path)
    suffix = timestamp.replace(":", "").replace("-", "")
    new_path = os.path.join(data_dir, f"models_{suffix}.json")
    os.rename(path, new_path)
    print(f"Renamed {path} -> {new_path}")


def load_price_history(data_dir: str) -> list[dict]:
    rename_untimestamped_snapshot(data_dir)
    rows: list[dict] = []
    last_models: dict[str, list] = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "models_*.json"))):
        with open(path) as f:
            data = json.load(f)
        timestamp = resolve_timestamp(data, path)
        for provider, provider_data in data.get("providers", {}).items():
            models = provider_data.get("models", [])
            if last_models.get(provider) == models:
                continue
            last_models[provider] = models
            rows.extend(flatten_snapshot({"providers": {provider: provider_data}}, timestamp))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", help="Directory containing models_*.json snapshots")
    parser.add_argument("--out", default="model_prices.csv", help="Output CSV path")
    args = parser.parse_args()

    import pandas as pd

    rows = load_price_history(args.data_dir)
    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)
    df.to_csv(args.out, index=False)

    n_snapshots = df["timestamp"].nunique()
    n_models = df[["provider", "model_id"]].drop_duplicates().shape[0]
    print(f"Wrote {len(df)} rows ({n_snapshots} snapshots, {n_models} unique models) to {args.out}")


if __name__ == "__main__":
    main()
