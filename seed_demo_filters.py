#!/usr/bin/env python3
"""Seed demo data and preset filters into the production DB for manual GUI verification.

Run this BEFORE starting the GUI. Then launch the GUI to verify:
  1. Library combobox shows A-1
  2. Status combobox shows "可用"
  3. Due start = 2027-01-01, Due end = 2027-12-31
  4. Table only shows X001 and X003 (filtered by A-1 + 可用 + date range)
  5. If user clicks export, the exported file only contains X001 + X003
"""

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from stage_lighting_tool import (
    DatabaseManager, LightingService,
    STATUS_AVAILABLE,
    SETTING_FILTER_LOCATION, SETTING_FILTER_STATUS,
    SETTING_FILTER_DUE_START, SETTING_FILTER_DUE_END,
)

DB_FILE = Path(__file__).parent / "stage_lighting.db"

def main():
    db = DatabaseManager(DB_FILE)
    svc = LightingService(db)

    for fn, m, loc, due, pic in [
        ("X001", "PAR64", "A-1", "2027-03-01", "张三"),
        ("X002", "LED200", "B-2", "2027-06-01", "李四"),
        ("X003", "Spot 200", "A-1", "2027-09-01", "王五"),
        ("X004", "Beam 300", "A-1", "2026-01-01", "赵六"),  # out of date range
    ]:
        try:
            svc.add_fixture(fn, m, "灯钩x1 / 电源线x1", loc, due, pic)
        except ValueError:
            # fixture already exists from prior demo run - skip
            pass

    # Borrow X002 so it has a different status for filter-demo contrast
    x002 = db.get_fixture_by_no("X002")
    if x002["status"] == STATUS_AVAILABLE:
        svc.borrow(x002["id"], "演示用户1", "演示借出")

    db.set_setting(SETTING_FILTER_LOCATION, "A-1")
    db.set_setting(SETTING_FILTER_STATUS, STATUS_AVAILABLE)
    db.set_setting(SETTING_FILTER_DUE_START, "2027-01-01")
    db.set_setting(SETTING_FILTER_DUE_END, "2027-12-31")

    # What the user should see on next startup:
    filters = {
        "location": db.get_setting(SETTING_FILTER_LOCATION),
        "status": db.get_setting(SETTING_FILTER_STATUS),
        "due_start": db.get_setting(SETTING_FILTER_DUE_START),
        "due_end": db.get_setting(SETTING_FILTER_DUE_END),
    }
    expected = svc.get_fixtures(**{k: (v or None) for k, v in filters.items()})
    print("=== Filter Persistence Demo Setup Complete ===")
    print(f"Stored filters: {filters}")
    print(f"Expected rows visible on startup: {len(expected)}")
    for fx in expected:
        print(f"  - {fx['fixture_no']} | {fx['location']} | {fx['status']} | {fx['inspection_due_date']}")
    print()
    print("Now start the GUI (python stage_lighting_tool.py) and verify:")
    print("  [1] Location combobox = A-1")
    print("  [2] Status combobox   = 可用")
    print("  [3] Due start = 2027-01-01, Due end = 2027-12-31")
    print("  [4] Table shows exactly 2 rows: X001, X003")
    print("  [5] Export CSV/JSON → matches the 2 visible rows")

    db.close()

if __name__ == "__main__":
    main()
