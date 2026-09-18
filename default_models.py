"""List the models that default_models.txt currently resolves to."""

import argparse
import csv
from pathlib import Path

from generate_price_chart import read_default_models_file

HERE = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(HERE / "model_prices.csv"), help="Price history CSV")
    parser.add_argument("--defaults-file", default=str(HERE / "default_models.txt"), help="Defaults list to resolve")
    args = parser.parse_args()

    with open(args.csv, newline="") as f:
        valid_labels = {f"{row['provider']}/{row['model_id']}" for row in csv.DictReader(f)}

    matched = read_default_models_file(Path(args.defaults_file), valid_labels)
    for label in matched:
        print(label)


if __name__ == "__main__":
    main()
