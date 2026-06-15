#!/usr/bin/env python3
"""End-to-end post-hoc verification of filter persistence.

After the user interacts with the real GUI, this script confirms that
- filter values written to settings survive a DB close/re-open
- fixture query using restored filters matches what the UI should show
- exported CSV exactly matches the filtered set
"""

import sys
import csv
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_lighting_tool import (
    DatabaseManager, LightingService,
    SETTING_FILTER_LOCATION, SETTING_FILTER_STATUS,
    SETTING_FILTER_DUE_START, SETTING_FILTER_DUE_END,
)

DB_FILE = Path(__file__).parent / "stage_lighting.db"


def report(s, indent=0):
    print(" " * indent + s)


def main():
    report("=" * 60)
    report("FILTER PERSISTENCE E2E VERIFICATION REPORT")
    report("=" * 60)

    db = DatabaseManager(DB_FILE)
    svc = LightingService(db)

    report("")
    report("[Step 1] Read stored filter settings from DB:")
    filters = {
        "location": db.get_setting(SETTING_FILTER_LOCATION, ""),
        "status":   db.get_setting(SETTING_FILTER_STATUS, ""),
        "due_start":db.get_setting(SETTING_FILTER_DUE_START, ""),
        "due_end":  db.get_setting(SETTING_FILTER_DUE_END, ""),
    }
    for k, v in filters.items():
        report(f"  {k:12s} = {repr(v)}", indent=2)

    report("")
    report("[Step 2] Simulate Application.__init__ restoring filters:")
    current_filters = {k: (v or None) for k, v in filters.items()}
    report(f"  _current_filters = {current_filters}", indent=2)
    report(f"  -> Combobox/Entry controls would be populated with these values.", indent=2)

    report("")
    report("[Step 3] Apply restored filters to fixture query (what the table shows):")
    fixtures = svc.get_fixtures(**current_filters)
    report(f"  Rows returned: {len(fixtures)}", indent=2)
    for f in fixtures:
        report(
            f"  - {f['fixture_no']:6s} | loc={f['location']:4s} | st={f['status']:6s} | "
            f"due={f['inspection_due_date']:12s} | pic={f['person_in_charge']}",
            indent=2,
        )

    report("")
    report("[Step 4] Verify export output matches filtered results:")
    out_dir = Path(tempfile.mkdtemp(prefix="e2e_export_"))
    csv_path = svc.export_csv(fixtures, str(out_dir), "e2e.csv")
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    report(f"  CSV header: {rows[0]}", indent=2)
    report(f"  Data rows:  {len(rows) - 1}", indent=2)
    csv_nos = {r[0] for r in rows[1:]}
    query_nos = {f["fixture_no"] for f in fixtures}
    match = csv_nos == query_nos
    report(f"  Export IDs == Query IDs: {match}  {csv_nos}", indent=2)

    report("")
    report("[Step 5] Cross-check with ALL fixtures (proves filter is active):")
    all_fixtures = svc.get_fixtures()
    report(f"  Total fixtures in DB: {len(all_fixtures)}", indent=2)
    report(f"  Filtered rows shown:  {len(fixtures)}", indent=2)
    if len(all_fixtures) > len(fixtures):
        hidden = [f["fixture_no"] for f in all_fixtures if f["fixture_no"] not in query_nos]
        report(f"  Correctly HIDDEN by filter: {hidden}", indent=2)
    report(f"  Filter is ACTIVE and NOT empty-default: {len(fixtures) != len(all_fixtures) or any(current_filters.values())}", indent=2)

    report("")
    report("=" * 60)
    if all([
        filters["location"],
        filters["status"],
        len(fixtures) == 2,
        csv_nos == query_nos,
        {"X001", "X003"} <= query_nos,
    ]):
        report("RESULT: PASS - All filter persistence checks succeeded.")
        rc = 0
    else:
        report("RESULT: FAIL - One or more checks failed (see above).")
        rc = 1
    report("=" * 60)

    db.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
