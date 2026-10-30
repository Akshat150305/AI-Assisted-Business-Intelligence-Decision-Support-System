"""One-command refresh: regenerate the synthetic data, clean it, build the database, export for
Power BI, document the KPIs, run the anomaly detection, generate the findings, write the Power BI specification, and produce the report, summary and slide content.

    python run_pipeline.py

Phase 8 adds the README and QA.
ALL DATA IS SYNTHETIC.
"""
import sys

from app_config import config
from src import (anomaly_detection, anomaly_evaluation, data_generator, data_pipeline, database,
                 kpi_catalogue, metrics, powerbi_export, powerbi_spec, recommendations, reporting)


def main():
    print("=== Step 1/7: generate synthetic raw data ===")
    data_generator.write_datasets(data_generator.build_datasets())
    print("raw data written.\n")

    print("=== Step 2/7: validate and clean ===")
    data_pipeline.main()

    print("\n=== Step 3/7: build SQLite database ===")
    database.main()

    print("\n=== Step 4/7: export Power BI tables ===")
    powerbi_export.main()

    print("\n=== Step 5/7: KPI catalogue and anomaly detection ===")
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


    print("\n=== Step 6/7: findings and Power BI specification ===")
    findings = recommendations.run(clean, detections)
    print(f"findings               : {len(findings)} -> {config.FINDINGS_EXPORT.name}, {config.FINDINGS_REPORT.name}")
    for _, f in findings.head(5).iterrows():
        print(f"  #{f['rank']} [{f['confidence']}] {f['title']}")
    powerbi_spec.write_all(clean, detections)
    print(f"power bi spec written  : {powerbi_spec.POWERBI_DIR.name}/ (4 documents; numbers from this run)")

    print("\n=== Step 7/7: business report, analytical summary and slide content ===")
    facts = reporting.collect_facts(clean, detections, findings, quality=reporting.quality_from_files(), evaluation=s)
    written = reporting.write_all(facts)
    print(f"report                 : {written['report'].relative_to(config.PROJECT_ROOT)}")
    print(f"analytical summary     : {written['summary'].relative_to(config.PROJECT_ROOT)} ({written['summary_rows']:,} rows)")
    print(f"slide content          : {written['slides'].relative_to(config.PROJECT_ROOT)}")

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
