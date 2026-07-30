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
  into `index.html`, the file published via GitHub Pages
- `default_models.txt` — models preselected when the chart loads
- `generate.sh` — runs the full pipeline (refresh + render)

## Usage

```sh
./generate.sh
```

This re-runs `load_model_prices.py` (renaming any un-timestamped
`data/models.json` snapshot along the way) and then `generate_price_chart.py`
to rebuild `index.html`. Extra arguments are passed through to
`generate_price_chart.py`, e.g. `./generate.sh --no-open`.

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
