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
    """Read default model labels from a file.

    Each non-comment line is matched against the available labels. Lines may be
    exact labels ("openrouter/openai/gpt-4o") or partial substrings
    ("gpt-4o", "claude-sonnet") — a partial line matches every label that
    contains it. Deduplicates while preserving file order.
    """
    if not path.exists():
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for line in path.read_text().splitlines():
        label = line.split("#", 1)[0].strip()
        if not label:
            continue
        matches = sorted(l for l in valid_labels if label in l)
        if not matches:
            print(f"warning: {path.name}: {label!r} matched no model in current data, skipping", file=sys.stderr)
            continue
        for m in matches:
            if m not in seen:
                seen.add(m)
                labels.append(m)
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


def canonical_key(model_id: str) -> str:
    """Normalize a provider model id to a bare model name for matching.

    Drops the vendor/backend prefix (everything before the last "/"),
    lowercases, and strips @region and :variant suffixes (batch, free, flex,
    ...) so that e.g. "moonshotai/kimi-k2.5", "moonshot/kimi-k2.5",
    "deepinfra/moonshotai/Kimi-K2.5" and "bedrock/kimi-k2.5@us-east-1" all
    map to "kimi-k2.5". Matching is done on this bare name because providers
    disagree on vendor prefixes (OpenRouter: "moonshotai", Requesty:
    "moonshot"/"bedrock"/"deepinfra").
    """
    name = model_id.lower().lstrip("~")
    name = name.rsplit("/", 1)[-1]
    name = name.split("@", 1)[0]
    name = name.split(":", 1)[0]
    return name


def canonical_display(model_id: str) -> str:
    """Display form retaining one vendor/model slash for context.

    Keeps the last two path segments (so "deepinfra/moonshotai/Kimi-K2.5"
    -> "moonshotai/kimi-k2.5"), lowercases, and strips leading ~ and
    @region/:variant suffixes. Falls back to the bare name if there's no slash.
    """
    name = model_id.lower().lstrip("~")
    name = name.split("@", 1)[0]
    name = name.split(":", 1)[0]
    parts = name.rsplit("/", 2)
    return "/".join(parts[-2:]) if len(parts) > 1 else parts[-1]


def _pick_or_price(
    entries: list[tuple[str, float, float]],
) -> tuple[str, float, float]:
    """Pick the OpenRouter (model_id, in, out) to display for a canonical key.

    Prefers the standard variant (no :batch/:free/:... suffix); among those,
    the cheapest by input cost.
    """
    standard = [e for e in entries if ":" not in e[0] and "@" not in e[0]]
    pool = standard or entries
    return min(pool, key=lambda e: e[1])


def _pick_rq_price(
    entries: list[tuple[str, float, float]],
) -> tuple[str, float, float]:
    """Pick the Requesty (model_id, in, out) for a canonical key: cheapest route."""
    return min(entries, key=lambda e: e[1])


def build_compare_rows(df: pd.DataFrame) -> list[dict[str, object]]:
    """Compare latest Requesty vs OpenRouter prices for models present on both,
    matching on the canonical model name so differently-prefixed ids (e.g.
    OpenRouter "moonshotai/kimi-k2.5" vs Requesty "moonshot/kimi-k2.5" or
    "bedrock/kimi-k2.5@us-east-1") still line up."""
    or_raw = latest_prices(df, "openrouter")
    rq_raw = latest_prices(df, "requesty")

    or_by_key: dict[str, list[tuple[str, float, float]]] = {}
    for mid, (inp, out) in or_raw.items():
        or_by_key.setdefault(canonical_key(mid), []).append((mid, inp, out))
    rq_by_key: dict[str, list[tuple[str, float, float]]] = {}
    for mid, (inp, out) in rq_raw.items():
        rq_by_key.setdefault(canonical_key(mid), []).append((mid, inp, out))

    shared = sorted(set(or_by_key) & set(rq_by_key), key=str.lower)
    rows = []
    for key in shared:
        or_id, or_in, or_out = _pick_or_price(or_by_key[key])
        rq_id, rq_in, rq_out = _pick_rq_price(rq_by_key[key])
        delta_in = (rq_in - or_in) / or_in * 100 if or_in else 0.0
        delta_out = (rq_out - or_out) / or_out * 100 if or_out else 0.0
        rows.append({
            "model": canonical_display(or_id),
            "or_id": or_id,
            "or_in": or_in,
            "or_out": or_out,
            "rq_id": rq_id,
            "rq_in": rq_in,
            "rq_out": rq_out,
            "rq_routes": len(rq_by_key[key]),
            "delta_in": delta_in,
            "delta_out": delta_out,
        })
    # Sort by OpenRouter input cost descending (most expensive first).
    rows.sort(key=lambda r: r["or_in"], reverse=True)
    return rows


def find_price_changes(df: pd.DataFrame) -> dict[str, list[str]]:
    """Map each snapshot timestamp to human-readable lines for models added,
    removed, or re-priced versus the previous snapshot of the same provider.

    The CSV is sparse per provider: load_model_prices.py records a provider's
    full model list only at snapshots where that provider's list changed, and
    skips identical intervening snapshots. So presence/absence must be
    compared within each provider's own change-point sequence, not across
    globally-adjacent timestamps (a label absent from a CSV row may simply
    belong to a provider that was not re-recorded, not have been removed)."""
    df = df.copy()
    df["label"] = df["provider"] + "/" + df["model_id"]
    changes: dict[str, list[str]] = {}

    for _provider, sub in df.sort_values("timestamp").groupby("provider"):
        # One row per (label, timestamp); keep the last price if duplicated.
        sub = sub.drop_duplicates(subset=["label", "timestamp"], keep="last")
        models_at: dict[str, dict[str, tuple[float, float]]] = {}
        for _, row in sub.iterrows():
            models_at.setdefault(row["timestamp"], {})[row["label"]] = (
                float(row["input_cost"]),
                float(row["output_cost"]),
            )

        prev_models: dict[str, tuple[float, float]] = {}
        prev_ts: str | None = None
        for ts in sorted(models_at):
            curr_models = models_at[ts]
            if prev_ts is not None:
                # New this snapshot.
                for label in sorted(set(curr_models) - set(prev_models)):
                    i, o = curr_models[label]
                    changes.setdefault(ts, []).append(
                        f"+ {label}: added — input ${i:g}, output ${o:g} (per Mtok)"
                    )
                # Gone since the last snapshot of this provider.
                for label in sorted(set(prev_models) - set(curr_models)):
                    i, o = prev_models[label]
                    changes.setdefault(ts, []).append(
                        f"- {label}: removed — was input ${i:g}, output ${o:g} (per Mtok)"
                    )
                # Present in both, with a changed price.
                for label in sorted(set(curr_models) & set(prev_models)):
                    pi, po = prev_models[label]
                    ci, co = curr_models[label]
                    if ci != pi or co != po:
                        changes.setdefault(ts, []).append(
                            f"{label}: input ${pi:g} → ${ci:g}, "
                            f"output ${po:g} → ${co:g} (per Mtok)"
                        )
            prev_ts = ts
            prev_models = curr_models

    # Stable, alphabetical ordering within each entry for reproducible diffs.
    return {ts: sorted(lines) for ts, lines in changes.items()}


def render_rss(df: pd.DataFrame) -> str:
    df = df.copy()
    df["label"] = df["provider"] + "/" + df["model_id"]
    changes = find_price_changes(df)

    items = []
    for ts in sorted(changes, reverse=True):
        lines = changes[ts]
        pub_date = datetime.fromisoformat(ts).replace(tzinfo=UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
        title = f"Model changes – {ts[:10]} ({len(lines)} model{'s' if len(lines) != 1 else ''})"
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
        title = f"Model changes – {ts[:10]} ({len(lines)} model{'s' if len(lines) != 1 else ''})"
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

    series = build_series(df)
    all_labels = sorted(series.keys())
    defaults = read_default_models_file(defaults_file, set(all_labels))
    if not defaults:
        defaults = default_labels_by_price_change(df, max_default)
    compare_rows = build_compare_rows(df)

    plotlyjs = plotly.offline.get_plotlyjs()
    template = (HERE / "price_chart_template.html").read_text()
    return (
        template.replace("__PLOTLYJS__", plotlyjs)
        .replace("__DATA__", json.dumps(series, separators=(",", ":")))
        .replace("__ALL_LABELS__", json.dumps(all_labels))
        .replace("__DEFAULT_LABELS__", json.dumps(defaults))
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
