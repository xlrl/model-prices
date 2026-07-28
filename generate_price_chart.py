"""Build a self-contained interactive HTML chart of model price history.

Reads model_prices.csv (produced by load_model_prices.py), embeds the data and
plotly.js directly in a single HTML file with a custom autocomplete multi-select
so it opens and works fully offline, no Jupyter/kernel required.

Writes index.html by default so the output can be published directly via
GitHub Pages (https://xlrl.github.io/model-prices/).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.offline

# dataviz skill reference palette: fixed hue order, CVD-validated.
PALETTE = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]

HERE = Path(__file__).parent


def read_default_models_file(path: Path, valid_labels: set[str]) -> list[str]:
    if not path.exists():
        return []
    labels = []
    for line in path.read_text().splitlines():
        label = line.split("#", 1)[0].strip()
        if not label:
            continue
        if label not in valid_labels:
            print(f"warning: {path.name}: {label!r} not found in current data, skipping", file=sys.stderr)
            continue
        labels.append(label)
    return labels


def default_labels_by_price_change(df: pd.DataFrame, max_default: int) -> list[str]:
    changed = (
        df.groupby("label")
        .agg(n_snapshots=("timestamp", "nunique"), n_prices=("input_cost", "nunique"))
        .query("n_prices > 1")
        .sort_values("n_snapshots", ascending=False)
    )
    return list(changed.head(max_default).index)


def build_series(df: pd.DataFrame) -> dict[str, list[list[float | str]]]:
    series: dict[str, list[list[float | str]]] = {}
    for label, sub in df.sort_values("timestamp").groupby("label"):
        series[label] = sub[["timestamp", "input_cost", "output_cost"]].values.tolist()
    return series


def render_html(df: pd.DataFrame, max_default: int, defaults_file: Path) -> str:
    df = df.copy()
    df["label"] = df["provider"] + "/" + df["model_id"]

    series = build_series(df)
    all_labels = sorted(series.keys())
    defaults = read_default_models_file(defaults_file, set(all_labels))
    if not defaults:
        defaults = default_labels_by_price_change(df, max_default)
    plotlyjs = plotly.offline.get_plotlyjs()

    template = (HERE / "price_chart_template.html").read_text()
    return (
        template.replace("__PLOTLYJS__", plotlyjs)
        .replace("__DATA__", json.dumps(series, separators=(",", ":")))
        .replace("__ALL_LABELS__", json.dumps(all_labels))
        .replace("__DEFAULT_LABELS__", json.dumps(defaults))
        .replace("__PALETTE__", json.dumps(PALETTE))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(HERE / "model_prices.csv"), help="Input CSV from load_model_prices.py")
    parser.add_argument("--out", default=str(HERE / "index.html"), help="Output HTML path")
    parser.add_argument("--max-default", type=int, default=6, help="Number of models preselected if defaults-file is empty/missing")
    parser.add_argument("--defaults-file", default=str(HERE / "default_models.txt"), help="Editable list of default models to preselect")
    parser.add_argument("--refresh", action="store_true", help="Re-run load_model_prices.py before rendering")
    parser.add_argument("--no-open", action="store_true", help="Don't launch xdg-open after writing the file")
    args = parser.parse_args()

    if args.refresh:
        subprocess.run(["uv", "run", "load_model_prices.py", "--out", args.csv], check=True, cwd=HERE)

    df = pd.read_csv(args.csv)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S")
    html = render_html(df, args.max_default, Path(args.defaults_file))
    Path(args.out).write_text(html)
    print(f"Wrote {args.out}")

    if not args.no_open:
        try:
            result = subprocess.run(["xdg-open", args.out], check=False)
            failed = result.returncode != 0
        except FileNotFoundError:
            failed = True
        if failed:
            print(f"xdg-open unavailable or failed; open manually: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
