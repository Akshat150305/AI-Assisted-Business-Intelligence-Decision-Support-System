"""One-command refresh: regenerate the synthetic data and clean it.

    python run_pipeline.py

Later phases add their steps here (database, KPIs, anomalies, findings, Power BI exports).
ALL DATA IS SYNTHETIC.
"""
import sys

from src import data_generator, data_pipeline


def main():
    print("=== Step 1/2: generate synthetic raw data ===")
    data_generator.write_datasets(data_generator.build_datasets())
    print("raw data written.\n")

    print("=== Step 2/2: validate and clean ===")
    data_pipeline.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
