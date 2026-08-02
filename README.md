# model-prices

Tracks LLM API pricing over time and renders it as an interactive, offline-capable
HTML chart.

**Live chart:** <https://xlrl.github.io/model-prices/>

Data points mark the arbitrary, irregular times a snapshot of the model list was
pulled (`data/models_<timestamp>.json`) — not a fixed sampling schedule. A gap
between points means no snapshot was taken, not that the price was stable.

## Layout

- `data/models_<timestamp>.json` — raw snapshots of provider/model pricing
- `load_model_prices.py` — flattens the snapshots into `model_prices.csv`
- `generate_price_chart.py` — renders `model_prices.csv` + `price_chart_template.html`
  into `index.html`, the file published via GitHub Pages, and also writes `feed.xml`
  and `atom.xml`, RSS and Atom feeds of price changes linked and badged from `index.html`
- `default_models.txt` — models preselected when the chart loads
- `generate.sh` — runs the full pipeline (refresh + render)

## Usage

```sh
./generate.sh
```

This re-runs `load_model_prices.py` (renaming any un-timestamped
`data/models.json` snapshot along the way) and then `generate_price_chart.py`
to rebuild `index.html`, `feed.xml`, and `atom.xml`. Extra arguments are passed
through to `generate_price_chart.py`, e.g. `./generate.sh --max-default 8`.

Pass `--commit-and-push` to have `generate.sh` stage `index.html`, `feed.xml`,
`atom.xml`, `model_prices.csv`, and `data/`, then commit and push — but only if
that staging actually produced a diff (a re-run with no real price changes is
a no-op, since the feeds' build dates are derived from the data, not
wall-clock time).

To run the steps individually:

```sh
uv run load_model_prices.py
uv run generate_price_chart.py
```

Pass `--refresh` to `generate_price_chart.py` to re-run `load_model_prices.py`
first. Run `uv run pytest` for tests and `uvx ruff check` to lint.

## Publishing

GitHub Pages is configured to serve `index.html` from the root of the `main`
branch, so committing a freshly generated `index.html` is all that's needed to
update <https://xlrl.github.io/model-prices/>.

## License

[MIT](LICENSE)
