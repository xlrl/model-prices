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
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
import plotly.offline

SITE_URL = "https://xlrl.github.io/model-prices/"

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
    defaults = list(changed.head(max_default).index)
    if defaults:
        return defaults
    # Fallback: not enough history to pick by change; show the cheapest models
    # by output cost so a fresh provider (e.g. only one snapshot) isn't blank.
    latest_ts = df["timestamp"].max() if not df.empty else None
    if latest_ts is None:
        return []
    latest = df[df["timestamp"] == latest_ts].sort_values("output_cost")
    return list(latest.head(max_default)["label"])


def build_series(df: pd.DataFrame) -> dict[str, list[list[float | str]]]:
    series: dict[str, list[list[float | str]]] = {}
    for label, sub in df.sort_values("timestamp").groupby("label"):
        series[label] = sub[["timestamp", "input_cost", "output_cost"]].values.tolist()
    return series


def latest_prices(df: pd.DataFrame, provider: str) -> dict[str, tuple[float, float]]:
    """Return {model_id: (input_cost, output_cost)} for the provider's newest snapshot."""
    sub = df[df["provider"] == provider]
    if sub.empty:
        return {}
    latest_ts = sub["timestamp"].max()
    latest = sub[sub["timestamp"] == latest_ts]
    return {
        row["model_id"]: (float(row["input_cost"]), float(row["output_cost"]))
        for _, row in latest.iterrows()
    }


def build_compare_rows(df: pd.DataFrame) -> list[dict[str, object]]:
    """Compare latest Requesty vs OpenRouter prices for models present on both."""
    or_prices = latest_prices(df, "openrouter")
    rq_prices = latest_prices(df, "requesty")
    shared = sorted(set(or_prices) & set(rq_prices), key=str.lower)
    rows = []
    for model_id in shared:
        or_in, or_out = or_prices[model_id]
        rq_in, rq_out = rq_prices[model_id]
        delta_in = (rq_in - or_in) / or_in * 100 if or_in else 0.0
        delta_out = (rq_out - or_out) / or_out * 100 if or_out else 0.0
        rows.append({
            "model": model_id,
            "or_in": or_in,
            "or_out": or_out,
            "rq_in": rq_in,
            "rq_out": rq_out,
            "delta_in": delta_in,
            "delta_out": delta_out,
        })
    # Sort by OpenRouter input cost descending (most expensive first).
    rows.sort(key=lambda r: r["or_in"], reverse=True)
    return rows


def find_price_changes(df: pd.DataFrame) -> dict[str, list[str]]:
    """Map each snapshot timestamp to a list of human-readable price change lines."""
    changes: dict[str, list[str]] = {}
    for label, sub in df.sort_values("timestamp").groupby("label"):
        sub = sub.drop_duplicates(subset="timestamp")
        prev_input = prev_output = None
        for _, row in sub.iterrows():
            if prev_input is not None and (row["input_cost"] != prev_input or row["output_cost"] != prev_output):
                line = (
                    f"{label}: input ${prev_input:g} → ${row['input_cost']:g}, "
                    f"output ${prev_output:g} → ${row['output_cost']:g} (per Mtok)"
                )
                changes.setdefault(row["timestamp"], []).append(line)
            prev_input, prev_output = row["input_cost"], row["output_cost"]
    return changes


def render_rss(df: pd.DataFrame) -> str:
    df = df.copy()
    df["label"] = df["provider"] + "/" + df["model_id"]
    changes = find_price_changes(df)

    items = []
    for ts in sorted(changes, reverse=True):
        lines = changes[ts]
        pub_date = datetime.fromisoformat(ts).replace(tzinfo=UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
        title = f"Price changes – {ts[:10]} ({len(lines)} model{'s' if len(lines) != 1 else ''})"
        list_items = "".join(f"<li>{escape(line)}</li>" for line in lines)
        items.append(f"""  <item>
    <title>{escape(title)}</title>
    <link>{escape(SITE_URL)}</link>
    <guid isPermaLink="false">model-prices-{ts}</guid>
    <pubDate>{pub_date}</pubDate>
    <description><![CDATA[<ul>{list_items}</ul>]]></description>
  </item>""")

    newest_ts = max(changes) if changes else None
    build_date = (
        datetime.fromisoformat(newest_ts).replace(tzinfo=UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
        if newest_ts
        else datetime.fromtimestamp(0, UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
    )
    items_xml = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Model price history</title>
  <link>{escape(SITE_URL)}</link>
  <description>Price changes tracked for LLM API models.</description>
  <lastBuildDate>{build_date}</lastBuildDate>
{items_xml}
</channel>
</rss>
"""


def render_atom(df: pd.DataFrame) -> str:
    df = df.copy()
    df["label"] = df["provider"] + "/" + df["model_id"]
    changes = find_price_changes(df)

    entries = []
    for ts in sorted(changes, reverse=True):
        lines = changes[ts]
        updated = datetime.fromisoformat(ts).replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = f"Price changes – {ts[:10]} ({len(lines)} model{'s' if len(lines) != 1 else ''})"
        list_items = "".join(f"<li>{escape(line)}</li>" for line in lines)
        entries.append(f"""  <entry>
    <title>{escape(title)}</title>
    <link href="{escape(SITE_URL)}"/>
    <id>tag:model-prices,{ts[:10]}:{ts}</id>
    <updated>{updated}</updated>
    <content type="html">{escape(f"<ul>{list_items}</ul>")}</content>
  </entry>""")

    newest_ts = max(changes) if changes else None
    feed_updated = (
        datetime.fromisoformat(newest_ts).replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if newest_ts
        else datetime.fromtimestamp(0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    entries_xml = "\n".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Model price history</title>
  <link href="{escape(SITE_URL)}"/>
  <link href="{escape(SITE_URL)}atom.xml" rel="self"/>
  <id>{escape(SITE_URL)}</id>
  <updated>{feed_updated}</updated>
{entries_xml}
</feed>
"""


def render_html(df: pd.DataFrame, max_default: int, defaults_file: Path) -> str:
    df = df.copy()
    df["label"] = df["provider"] + "/" + df["model_id"]

    main_providers = ("openrouter", "eurouter")
    main_df = df[df["provider"].isin(main_providers)]
    rq_df = df[df["provider"] == "requesty"]

    main_series = build_series(main_df)
    rq_series = build_series(rq_df)
    main_labels = sorted(main_series.keys())
    rq_labels = sorted(rq_series.keys())

    main_defaults = read_default_models_file(defaults_file, set(main_labels))
    if not main_defaults:
        main_defaults = default_labels_by_price_change(main_df, max_default)
    rq_defaults = default_labels_by_price_change(rq_df, max_default)

    compare_rows = build_compare_rows(df)

    plotlyjs = plotly.offline.get_plotlyjs()
    template = (HERE / "price_chart_template.html").read_text()
    return (
        template.replace("__PLOTLYJS__", plotlyjs)
        .replace("__MAIN_DATA__", json.dumps(main_series, separators=(",", ":")))
        .replace("__MAIN_ALL_LABELS__", json.dumps(main_labels))
        .replace("__MAIN_DEFAULT_LABELS__", json.dumps(main_defaults))
        .replace("__RQ_DATA__", json.dumps(rq_series, separators=(",", ":")))
        .replace("__RQ_ALL_LABELS__", json.dumps(rq_labels))
        .replace("__RQ_DEFAULT_LABELS__", json.dumps(rq_defaults))
        .replace("__COMPARE_ROWS__", json.dumps(compare_rows, separators=(",", ":")))
        .replace("__PALETTE__", json.dumps(PALETTE))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(HERE / "model_prices.csv"), help="Input CSV from load_model_prices.py")
    parser.add_argument("--out", default=str(HERE / "index.html"), help="Output HTML path")
    parser.add_argument("--rss-out", default=str(HERE / "feed.xml"), help="Output RSS feed path")
    parser.add_argument("--atom-out", default=str(HERE / "atom.xml"), help="Output Atom feed path")
    parser.add_argument("--max-default", type=int, default=6, help="Number of models preselected if defaults-file is empty/missing")
    parser.add_argument("--defaults-file", default=str(HERE / "default_models.txt"), help="Editable list of default models to preselect")
    parser.add_argument("--refresh", action="store_true", help="Re-run load_model_prices.py before rendering")
    args = parser.parse_args()

    if args.refresh:
        subprocess.run(["uv", "run", "load_model_prices.py", "--out", args.csv], check=True, cwd=HERE)

    df = pd.read_csv(args.csv)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S")
    html = render_html(df, args.max_default, Path(args.defaults_file))
    Path(args.out).write_text(html)
    print(f"Wrote {args.out}")

    rss = render_rss(df)
    Path(args.rss_out).write_text(rss)
    print(f"Wrote {args.rss_out}")

    atom = render_atom(df)
    Path(args.atom_out).write_text(atom)
    print(f"Wrote {args.atom_out}")


if __name__ == "__main__":
    main()
