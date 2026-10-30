"""One-command refresh: regenerate the synthetic data, clean it, build the database, export for
Power BI, document the KPIs, run the anomaly detection, generate the findings and write the Power BI specification.

    python run_pipeline.py

Later phases add their steps here (report).
ALL DATA IS SYNTHETIC.
"""
import sys

from app_config import config
from src import (anomaly_detection, anomaly_evaluation, data_generator, data_pipeline, database,
                 kpi_catalogue, metrics, powerbi_export, powerbi_spec, recommendations)


def main():
    print("=== Step 1/6: generate synthetic raw data ===")
    data_generator.write_datasets(data_generator.build_datasets())
    print("raw data written.\n")

    print("=== Step 2/6: validate and clean ===")
    data_pipeline.main()

    print("\n=== Step 3/6: build SQLite database ===")
    database.main()

    print("\n=== Step 4/6: export Power BI tables ===")
    powerbi_export.main()

    print("\n=== Step 5/6: KPI catalogue and anomaly detection ===")
    clean = database.load_clean_sales()
    kpi_catalogue.main()
    detections, result = anomaly_evaluation.run(clean)       # scoring vs planted labels happens AFTER detection
    rows = anomaly_detection.export_weekly(detections)
    s = result["summary"]
    print(f"anomaly table exported : {rows:,} series-weeks -> {config.ANOMALY_WEEKLY_EXPORT.name}")
    print(f"flags                  : {s['flags']} of {s['evaluated_series_weeks']:,} scored series-weeks "
          f"({s['flag_rate_pct']:.2f}%)")
    print(f"planted events found   : {s['events_detected']} of {s['events']}  "
          f"(evaluation only; 4 events is too few to estimate accuracy)")
    print(f"evaluation report      : {config.ANOMALY_EVALUATION_REPORT}")


    print("\n=== Step 6/6: findings and Power BI specification ===")
    findings = recommendations.run(clean, detections)
    print(f"findings               : {len(findings)} -> {config.FINDINGS_EXPORT.name}, {config.FINDINGS_REPORT.name}")
    for _, f in findings.head(5).iterrows():
        print(f"  #{f['rank']} [{f['confidence']}] {f['title']}")
    powerbi_spec.write_all(clean, detections)
    print(f"power bi spec written  : {powerbi_spec.POWERBI_DIR.name}/ (4 documents; numbers from this run)")

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
