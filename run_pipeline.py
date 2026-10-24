"""One-command refresh: regenerate the synthetic data, clean it, build the database, export for Power BI.

    python run_pipeline.py

Later phases add their steps here (anomaly detection, findings, report).
ALL DATA IS SYNTHETIC.
"""
import sys

from src import data_generator, data_pipeline, database, metrics, powerbi_export


def main():
    print("=== Step 1/4: generate synthetic raw data ===")
    data_generator.write_datasets(data_generator.build_datasets())
    print("raw data written.\n")

    print("=== Step 2/4: validate and clean ===")
    data_pipeline.main()

    print("\n=== Step 3/4: build SQLite database ===")
    database.main()

    print("\n=== Step 4/4: export Power BI tables ===")
    powerbi_export.main()

    clean = database.load_clean_sales()
    kpis = metrics.compute_kpis(clean)
    print("\n=== Headline KPIs (all clean data, synthetic) ===")
    print(f"revenue          : {kpis['revenue']:,.2f}")
    print(f"profit           : {kpis['profit']:,.2f}")
    print(f"profit margin    : {kpis['profit_margin_pct']:.2f}%")
    print(f"distinct orders  : {kpis['distinct_orders']:,}")
    print(f"avg order value  : {kpis['avg_order_value']:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
