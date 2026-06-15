#!/usr/bin/env python3
"""Tests for Stage Lighting Tool - business logic, state machine, persistence, export"""

import os
import sys
import json
import csv
import sqlite3
import tempfile
import shutil
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from stage_lighting_tool import (
    DatabaseManager, LightingService,
    STATUS_AVAILABLE, STATUS_BORROWED, STATUS_RETURN_PENDING,
    STATUS_INSPECTION_FREEZE, STATUS_MAINTENANCE_FREEZE, STATUS_SCRAPPED,
    SETTING_FILTER_LOCATION, SETTING_FILTER_STATUS, SETTING_FILTER_DUE_START, SETTING_FILTER_DUE_END,
    RESULT_NEW, RESULT_UPDATE, RESULT_SKIP, RESULT_ERROR, EXPORT_HEADERS,
)

TMP_DIR = None
_test_counter = 0


def setup():
    global TMP_DIR
    TMP_DIR = tempfile.mkdtemp(prefix="stage_lighting_test_")


def teardown():
    if TMP_DIR and os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR, ignore_errors=True)


def make_service():
    global _test_counter
    _test_counter += 1
    db_path = Path(TMP_DIR) / f"test_{_test_counter}.db"
    db = DatabaseManager(db_path)
    return LightingService(db), db


def test_add_fixture():
    svc, db = make_service()
    fid = svc.add_fixture("L001", "PAR64", "灯管x2", "A-1", "2027-01-01", "张三")
    f = db.get_fixture(fid)
    assert f["fixture_no"] == "L001"
    assert f["status"] == STATUS_AVAILABLE
    hist = db.get_history(fid)
    assert len(hist) == 1
    assert hist[0]["action"] == "添加灯具"
    db.close()
    print("  PASS test_add_fixture")


def test_add_duplicate_fixture():
    svc, db = make_service()
    svc.add_fixture("L001", "PAR64", "灯管x2", "A-1", "2027-01-01", "张三")
    try:
        svc.add_fixture("L001", "LED200", "灯泡x1", "A-2", "2027-01-01", "李四")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "已存在" in str(e)
    db.close()
    print("  PASS test_add_duplicate_fixture")


def test_borrow_flow():
    svc, db = make_service()
    fid = svc.add_fixture("L001", "PAR64", "", "A-1", "2027-01-01", "张三")
    svc.borrow(fid, "王五", "演出借用")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_BORROWED
    assert f["last_remark"] == "演出借用"
    hist = db.get_history(fid)
    assert any(h["action"] == "借出" for h in hist)
    db.close()
    print("  PASS test_borrow_flow")


def test_borrow_inspection_expired():
    svc, db = make_service()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    fid = svc.add_fixture("L002", "LED200", "", "A-2", yesterday, "张三")
    try:
        svc.borrow(fid, "王五", "想借")
        assert False, "Should have raised ValueError for expired inspection"
    except ValueError as e:
        assert "巡检已过期" in str(e)
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_AVAILABLE, f"Status should stay AVAILABLE, got {f['status']}"
    db.close()
    print("  PASS test_borrow_inspection_expired")


def test_return_and_review():
    svc, db = make_service()
    fid = svc.add_fixture("L003", "Spot", "", "B-1", "2027-06-01", "李四")
    svc.borrow(fid, "王五", "借用")
    svc.return_fixture(fid, "王五", "归还完好")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_RETURN_PENDING
    svc.review_return(fid, "李四", "复核通过")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_AVAILABLE
    hist = db.get_history(fid)
    actions = [h["action"] for h in hist]
    assert "借出" in actions
    assert "归还登记" in actions
    assert "复核入库" in actions
    db.close()
    print("  PASS test_return_and_review")


def test_duplicate_return():
    svc, db = make_service()
    fid = svc.add_fixture("L004", "Wash", "", "C-1", "2027-06-01", "赵六")
    svc.borrow(fid, "王五", "借用")
    svc.return_fixture(fid, "王五", "归还")
    try:
        svc.return_fixture(fid, "王五", "重复归还")
        assert False, "Should have raised ValueError for duplicate return"
    except ValueError as e:
        assert "重复提交" in str(e)
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_RETURN_PENDING, "Status must stay RETURN_PENDING on duplicate return"
    db.close()
    print("  PASS test_duplicate_return")


def test_inspection_freeze_unfreeze():
    svc, db = make_service()
    fid = svc.add_fixture("L005", "Flood", "", "D-1", "2026-01-01", "陈七")
    svc.freeze_inspection(fid, "管理员", "到期巡检")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_INSPECTION_FREEZE
    new_date = "2027-01-01"
    svc.unfreeze_inspection(fid, "陈七", new_date, "巡检完成")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_AVAILABLE
    assert f["inspection_due_date"] == new_date
    db.close()
    print("  PASS test_inspection_freeze_unfreeze")


def test_unfreeze_inspection_wrong_person():
    svc, db = make_service()
    fid = svc.add_fixture("L006", "Beam", "", "E-1", "2026-01-01", "陈七")
    svc.freeze_inspection(fid, "管理员", "到期巡检")
    try:
        svc.unfreeze_inspection(fid, "路人甲", "2027-01-01", "想解冻")
        assert False, "Should have raised ValueError for unauthorized unfreeze"
    except ValueError as e:
        assert "无权限" in str(e)
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_INSPECTION_FREEZE, "Status must not change on unauthorized unfreeze"
    db.close()
    print("  PASS test_unfreeze_inspection_wrong_person")


def test_maintenance_freeze_unfreeze():
    svc, db = make_service()
    fid = svc.add_fixture("L007", "Moving", "", "F-1", "2027-06-01", "周八")
    svc.freeze_maintenance(fid, "管理员", "灯泡损坏")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_MAINTENANCE_FREEZE
    svc.unfreeze_maintenance(fid, "周八", "维修完成")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_AVAILABLE
    db.close()
    print("  PASS test_maintenance_freeze_unfreeze")


def test_unfreeze_maintenance_wrong_person():
    svc, db = make_service()
    fid = svc.add_fixture("L008", "Follow", "", "G-1", "2027-06-01", "周八")
    svc.freeze_maintenance(fid, "管理员", "故障")
    try:
        svc.unfreeze_maintenance(fid, "路人乙", "想解冻")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "无权限" in str(e)
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_MAINTENANCE_FREEZE, "Status must not change on unauthorized unfreeze"
    db.close()
    print("  PASS test_unfreeze_maintenance_wrong_person")


def test_scrap():
    svc, db = make_service()
    fid = svc.add_fixture("L009", "Hazer", "", "H-1", "2027-06-01", "吴九")
    svc.scrap(fid, "吴九", "无法修复")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_SCRAPPED
    try:
        svc.borrow(fid, "吴九", "想借")
        assert False, "Scrapped item should not be borrowable"
    except ValueError:
        pass
    db.close()
    print("  PASS test_scrap")


def test_invalid_transition():
    svc, db = make_service()
    fid = svc.add_fixture("L010", "Strobe", "", "I-1", "2027-06-01", "吴九")
    try:
        svc.review_return(fid, "吴九", "直接复核")
        assert False, "Should not allow review from AVAILABLE"
    except ValueError as e:
        assert "不允许" in str(e) or "无需重复操作" in str(e)
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_AVAILABLE
    db.close()
    print("  PASS test_invalid_transition")


def test_filter_fixtures():
    svc, db = make_service()
    svc.add_fixture("F001", "PAR64", "", "A-1", "2027-01-01", "张三")
    svc.add_fixture("F002", "LED200", "", "A-2", "2027-03-01", "李四")
    svc.add_fixture("F003", "Spot", "", "B-1", "2027-06-01", "张三")
    results = svc.get_fixtures(location="A-1")
    assert len(results) == 1 and results[0]["fixture_no"] == "F001"
    results = svc.get_fixtures(status=STATUS_AVAILABLE)
    assert len(results) == 3
    results = svc.get_fixtures(due_start="2027-03-01", due_end="2027-06-01")
    assert len(results) == 2
    results = svc.get_fixtures(location="A-1", status=STATUS_AVAILABLE)
    assert len(results) == 1
    db.close()
    print("  PASS test_filter_fixtures")


def test_export_csv():
    svc, db = make_service()
    svc.add_fixture("E001", "PAR64", "灯管x2", "A-1", "2027-01-01", "张三")
    fixtures = svc.get_fixtures()
    out_dir = Path(TMP_DIR) / "export"
    out_dir.mkdir()
    path = svc.export_csv(fixtures, str(out_dir), "test.csv")
    assert os.path.exists(path)
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    assert rows[0][0] == "灯具编号"
    assert len(rows) == 2
    db.close()
    print("  PASS test_export_csv")


def test_export_json():
    svc, db = make_service()
    svc.add_fixture("E002", "LED", "灯泡x1", "B-1", "2027-01-01", "李四")
    fixtures = svc.get_fixtures()
    out_dir = Path(TMP_DIR) / "export2"
    out_dir.mkdir()
    path = svc.export_json(fixtures, str(out_dir), "test.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["fixture_no"] == "E002"
    db.close()
    print("  PASS test_export_json")


def test_export_unwritable_dir():
    svc, db = make_service()
    svc.add_fixture("E003", "Spot", "", "C-1", "2027-01-01", "王五")
    fixtures = svc.get_fixtures()
    fake_dir = os.path.join(TMP_DIR, "nonexistent_dir_xyz")
    try:
        svc.export_csv(fixtures, fake_dir, "test.csv")
        assert False, "Should have raised error for non-existent dir"
    except (PermissionError, FileNotFoundError):
        pass
    db.close()
    print("  PASS test_export_unwritable_dir")


def test_persistence_across_restart():
    db_path = Path(TMP_DIR) / "persist_test.db"
    db1 = DatabaseManager(db_path)
    svc1 = LightingService(db1)
    fid = svc1.add_fixture("P001", "PAR64", "灯管x2", "A-1", "2027-01-01", "张三")
    svc1.borrow(fid, "王五", "借用")
    svc1.set_export_dir("/tmp/test_exports")
    db1.close()

    db2 = DatabaseManager(db_path)
    svc2 = LightingService(db2)
    f = db2.get_fixture(fid)
    assert f["status"] == STATUS_BORROWED
    assert f["last_remark"] == "借用"
    hist = db2.get_history(fid)
    assert len(hist) >= 2
    assert svc2.get_export_dir() == "/tmp/test_exports"
    db2.close()
    print("  PASS test_persistence_across_restart")


def test_history_timeline():
    svc, db = make_service()
    fid = svc.add_fixture("H001", "Beam", "", "A-1", "2027-01-01", "张三")
    svc.borrow(fid, "王五", "演出用")
    svc.return_fixture(fid, "王五", "归还")
    svc.review_return(fid, "张三", "复核OK")
    hist = db.get_history(fid)
    actions = [h["action"] for h in hist]
    assert "添加灯具" in actions
    assert "借出" in actions
    assert "归还登记" in actions
    assert "复核入库" in actions
    for h in hist:
        if h["action"] == "借出":
            assert h["from_status"] == STATUS_AVAILABLE
            assert h["to_status"] == STATUS_BORROWED
    db.close()
    print("  PASS test_history_timeline")


def test_status_counts():
    svc, db = make_service()
    svc.add_fixture("S001", "A", "", "A-1", "2027-01-01", "张三")
    svc.add_fixture("S002", "B", "", "A-2", "2027-01-01", "李四")
    fid3 = svc.add_fixture("S003", "C", "", "A-3", "2027-01-01", "王五")
    svc.borrow(fid3, "路人", "借用")
    counts = svc.get_status_counts()
    assert counts.get(STATUS_AVAILABLE, 0) == 2
    assert counts.get(STATUS_BORROWED, 0) == 1
    db.close()
    print("  PASS test_status_counts")


def test_export_matches_filter():
    svc, db = make_service()
    svc.add_fixture("X001", "PAR", "", "A-1", "2027-01-01", "张三")
    svc.add_fixture("X002", "LED", "", "B-1", "2027-06-01", "李四")
    svc.add_fixture("X003", "Spot", "", "A-1", "2027-03-01", "王五")
    filtered = svc.get_fixtures(location="A-1")
    assert len(filtered) == 2
    out_dir = Path(TMP_DIR) / "export_filter"
    out_dir.mkdir()
    path = svc.export_csv(filtered, str(out_dir), "filtered.csv")
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 3  # header + 2 data rows
    nos = [rows[1][0], rows[2][0]]
    assert "X001" in nos and "X003" in nos
    db.close()
    print("  PASS test_export_matches_filter")


def test_borrow_from_borrowed_rejected():
    svc, db = make_service()
    fid = svc.add_fixture("B001", "PAR", "", "A-1", "2027-01-01", "张三")
    svc.borrow(fid, "王五", "第一次借")
    try:
        svc.borrow(fid, "赵六", "再借一次")
        assert False, "Should not allow borrowing an already borrowed item"
    except ValueError:
        pass
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_BORROWED
    db.close()
    print("  PASS test_borrow_from_borrowed_rejected")


def test_freeze_from_borrowed():
    svc, db = make_service()
    fid = svc.add_fixture("B002", "PAR", "", "A-1", "2027-01-01", "张三")
    svc.borrow(fid, "王五", "借出中")
    svc.freeze_inspection(fid, "管理员", "到期需巡检")
    f = db.get_fixture(fid)
    assert f["status"] == STATUS_INSPECTION_FREEZE
    db.close()
    print("  PASS test_freeze_from_borrowed")


def test_update_fixture_info():
    svc, db = make_service()
    fid = svc.add_fixture("U001", "OldModel", "旧配件", "A-1", "2027-01-01", "张三")
    svc.update_fixture_info(fid, "NewModel", "新配件", "B-2", "2027-12-01", "李四")
    f = db.get_fixture(fid)
    assert f["model"] == "NewModel"
    assert f["location"] == "B-2"
    assert f["person_in_charge"] == "李四"
    hist = db.get_history(fid)
    assert any(h["action"] == "编辑信息" for h in hist)
    db.close()
    print("  PASS test_update_fixture_info")


def _save_filters_to_db(db, location, status, due_start, due_end):
    db.set_setting(SETTING_FILTER_LOCATION, location or "")
    db.set_setting(SETTING_FILTER_STATUS, status or "")
    db.set_setting(SETTING_FILTER_DUE_START, due_start or "")
    db.set_setting(SETTING_FILTER_DUE_END, due_end or "")


def _make_filter_app(db_path):
    """Simulate Application startup to verify filter persistence without real Tk window.

    We check that:
      - settings in DB are read and properly translated into _current_filters
      - the fixture query using those filters returns the expected set
    """
    db = DatabaseManager(db_path)
    svc = LightingService(db)

    loc = db.get_setting(SETTING_FILTER_LOCATION, "")
    sta = db.get_setting(SETTING_FILTER_STATUS, "")
    ds = db.get_setting(SETTING_FILTER_DUE_START, "")
    de = db.get_setting(SETTING_FILTER_DUE_END, "")
    current_filters = {
        "location": loc or None,
        "status": sta or None,
        "due_start": ds or None,
        "due_end": de or None,
    }
    fixtures = svc.get_fixtures(**current_filters)
    return db, svc, current_filters, fixtures


def test_filter_persistence_rootcause_empty_on_start():
    """Reproduce the OLD bug: after user sets filters in UI and exits, re-open
    should find the filter settings saved in DB. Before fix they were never
    persisted so the below assertion would fail. This test now verifies the
    fix actually stores something on persist call.
    """
    db_path = Path(TMP_DIR) / f"rc_{_test_counter+1}.db"
    db1 = DatabaseManager(db_path)
    svc1 = LightingService(db1)
    svc1.add_fixture("R001", "PAR", "", "A-1", "2027-01-01", "张三")
    svc1.add_fixture("R002", "LED", "", "B-1", "2027-06-01", "李四")

    db1.set_setting(SETTING_FILTER_LOCATION, "A-1")
    db1.set_setting(SETTING_FILTER_STATUS, STATUS_AVAILABLE)
    db1.close()

    db2, svc2, cf, fixtures = _make_filter_app(db_path)
    assert cf["location"] == "A-1", f"Expected location filter to persist but got {cf['location']}"
    assert cf["status"] == STATUS_AVAILABLE
    assert len(fixtures) == 1 and fixtures[0]["fixture_no"] == "R001"
    db2.close()
    print("  PASS test_filter_persistence_rootcause_empty_on_start")


def test_filter_location_status_kept_across_restart():
    """Regression #1: 库位 + 状态筛选跨重启保留，并正确影响查询结果"""
    global _test_counter
    _test_counter += 1
    db_path = Path(TMP_DIR) / f"r1_{_test_counter}.db"
    db1 = DatabaseManager(db_path)
    svc1 = LightingService(db1)
    f1 = svc1.add_fixture("X001", "PAR64", "", "A-1", "2027-01-01", "张三")
    f2 = svc1.add_fixture("X002", "LED", "", "A-2", "2027-01-01", "李四")
    f3 = svc1.add_fixture("X003", "Spot", "", "A-1", "2027-03-01", "王五")
    svc1.borrow(f3, "用户X", "借走了")

    db1.set_setting(SETTING_FILTER_LOCATION, "A-1")
    db1.set_setting(SETTING_FILTER_STATUS, STATUS_AVAILABLE)
    db1.set_setting(SETTING_FILTER_DUE_START, "")
    db1.set_setting(SETTING_FILTER_DUE_END, "")
    db1.close()

    db2, svc2, cf, fixtures = _make_filter_app(db_path)
    assert cf["location"] == "A-1"
    assert cf["status"] == STATUS_AVAILABLE
    assert cf["due_start"] is None
    assert cf["due_end"] is None
    ids = {f["fixture_no"] for f in fixtures}
    assert ids == {"X001"}, f"Expected only X001 (A-1 + 可用), got {ids}"
    db2.close()
    print("  PASS test_filter_location_status_kept_across_restart")


def test_filter_due_range_effective_across_restart():
    """Regression #2: 到期范围筛选跨重启生效"""
    global _test_counter
    _test_counter += 1
    db_path = Path(TMP_DIR) / f"r2_{_test_counter}.db"
    db1 = DatabaseManager(db_path)
    svc1 = LightingService(db1)
    svc1.add_fixture("Y001", "PAR", "", "A-1", "2027-01-15", "张三")
    svc1.add_fixture("Y002", "LED", "", "B-1", "2027-04-01", "李四")
    svc1.add_fixture("Y003", "Spot", "", "C-1", "2027-08-01", "王五")

    db1.set_setting(SETTING_FILTER_LOCATION, "")
    db1.set_setting(SETTING_FILTER_STATUS, "")
    db1.set_setting(SETTING_FILTER_DUE_START, "2027-03-01")
    db1.set_setting(SETTING_FILTER_DUE_END, "2027-06-30")
    db1.close()

    db2, svc2, cf, fixtures = _make_filter_app(db_path)
    assert cf["due_start"] == "2027-03-01"
    assert cf["due_end"] == "2027-06-30"
    nos = [f["fixture_no"] for f in fixtures]
    assert nos == ["Y002"], f"Expected only Y002 within date range, got {nos}"
    db2.close()
    print("  PASS test_filter_due_range_effective_across_restart")


def test_export_uses_restored_filter_results():
    """Regression #3: 导出内容 = 恢复后的筛选结果（和用户重启后直接点导出看到的一致）"""
    global _test_counter
    _test_counter += 1
    db_path = Path(TMP_DIR) / f"r3_{_test_counter}.db"
    db1 = DatabaseManager(db_path)
    svc1 = LightingService(db1)
    svc1.add_fixture("Z001", "PAR", "", "A-1", "2027-01-01", "张三")
    svc1.add_fixture("Z002", "LED", "", "B-1", "2027-01-01", "李四")
    svc1.add_fixture("Z003", "Spot", "", "A-1", "2027-01-01", "王五")

    db1.set_setting(SETTING_FILTER_LOCATION, "A-1")
    db1.set_setting(SETTING_FILTER_STATUS, STATUS_AVAILABLE)
    db1.set_setting(SETTING_FILTER_DUE_START, "")
    db1.set_setting(SETTING_FILTER_DUE_END, "")
    db1.close()

    db2, svc2, cf, fixtures = _make_filter_app(db_path)
    assert len(fixtures) == 2

    out_dir = Path(TMP_DIR) / f"r3_export_{_test_counter}"
    out_dir.mkdir()
    csv_path = svc2.export_csv(fixtures, str(out_dir), "result.csv")
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 3  # header + 2 rows
    exported_nos = {rows[1][0], rows[2][0]}
    queried_nos = {f["fixture_no"] for f in fixtures}
    assert exported_nos == queried_nos, f"Export {exported_nos} != Query {queried_nos}"
    db2.close()
    print("  PASS test_export_uses_restored_filter_results")


def test_reset_clears_persisted_filters():
    """Bonus: 重置筛选后 DB 中的筛选设置也被清空"""
    global _test_counter
    _test_counter += 1
    db_path = Path(TMP_DIR) / f"r4_{_test_counter}.db"
    db = DatabaseManager(db_path)
    db.set_setting(SETTING_FILTER_LOCATION, "A-99")
    db.set_setting(SETTING_FILTER_STATUS, STATUS_BORROWED)
    db.set_setting(SETTING_FILTER_DUE_START, "2020-01-01")
    db.set_setting(SETTING_FILTER_DUE_END, "2020-12-31")

    db.set_setting(SETTING_FILTER_LOCATION, "")
    db.set_setting(SETTING_FILTER_STATUS, "")
    db.set_setting(SETTING_FILTER_DUE_START, "")
    db.set_setting(SETTING_FILTER_DUE_END, "")

    assert db.get_setting(SETTING_FILTER_LOCATION, "") == ""
    assert db.get_setting(SETTING_FILTER_STATUS, "") == ""
    assert db.get_setting(SETTING_FILTER_DUE_START, "") == ""
    assert db.get_setting(SETTING_FILTER_DUE_END, "") == ""
    db.close()
    print("  PASS test_reset_clears_persisted_filters")


def _make_import_csv(dirpath, rows, headers=None):
    import csv
    if headers is None:
        headers = ["灯具编号", "型号", "配件", "库位", "巡检到期日", "负责人", "状态", "最近备注"]
    filepath = dirpath / "import_test.csv"
    with open(filepath, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
    return filepath


def _make_import_json(dirpath, items):
    import json
    filepath = dirpath / "import_test.json"
    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    return filepath


def test_import_parse_csv():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "parse_csv"
    out_dir.mkdir()
    rows = [
        ["L100", "PAR64", "灯钩x1", "A-01", "2027-01-01", "张三", "", ""],
        ["L101", "LED200", "灯钩x1", "A-02", "2027-06-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    records = svc.parse_import_file(str(filepath))
    assert len(records) == 2
    assert records[0]["fixture_no"] == "L100"
    assert records[0]["model"] == "PAR64"
    assert records[0]["person_in_charge"] == "张三"
    db.close()
    print("  PASS test_import_parse_csv")


def test_import_parse_json():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "parse_json"
    out_dir.mkdir()
    items = [
        {"fixture_no": "L200", "model": "Beam", "person_in_charge": "王五", "location": "B-01"},
        {"fixture_no": "L201", "model": "Spot", "person_in_charge": "赵六", "inspection_due_date": "2027-12-01"},
    ]
    filepath = _make_import_json(out_dir, items)
    records = svc.parse_import_file(str(filepath))
    assert len(records) == 2
    assert records[0]["fixture_no"] == "L200"
    assert records[1]["inspection_due_date"] == "2027-12-01"
    db.close()
    print("  PASS test_import_parse_json")


def test_import_precheck_validation_errors():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "precheck_err"
    out_dir.mkdir()
    rows = [
        ["", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
        ["L301", "", "", "A-02", "2027-01-01", "李四", "", ""],
        ["L302", "LED", "", "A-03", "bad-date", "王五", "", ""],
        ["L303", "Beam", "", "A-04", "2027-01-01", "", "", ""],
        ["L304", "Spot", "", "A-05", "2027-01-01", "赵六", "无效状态", ""],
        ["L305", "L305dup", "", "A-06", "2027-01-01", "钱七", "", ""],
        ["L305", "L305dup", "", "A-07", "2027-01-01", "孙八", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    records = svc.parse_import_file(str(filepath))
    results, summary = svc.precheck_import(records)
    assert summary[RESULT_ERROR] == 6
    assert summary["total"] == 7
    errors_by_row = {}
    for r in results:
        if r["result"] == RESULT_ERROR:
            errors_by_row[str(r["row"])] = r["errors"]
    assert any("灯具编号不能为空" in e for e in errors_by_row.get("2", []))
    assert any("缺少必填字段: 型号" in e for e in errors_by_row.get("3", []))
    assert any("日期格式错误" in e for e in errors_by_row.get("4", []))
    assert any("缺少必填字段: 负责人" in e for e in errors_by_row.get("5", []))
    assert any("状态值无效" in e for e in errors_by_row.get("6", []))
    assert any("文件内编号重复" in e for e in errors_by_row.get("8", []))
    db.close()
    print("  PASS test_import_precheck_validation_errors")


def test_import_precheck_new_update_skip():
    svc, db = make_service()
    svc.add_fixture("L400", "OldModel", "old", "OldLoc", "2026-01-01", "OldPerson")
    svc.add_fixture("L401", "SameModel", "same", "SameLoc", "2027-01-01", "SamePerson")

    out_dir = Path(TMP_DIR) / "precheck_mix"
    out_dir.mkdir()
    rows = [
        ["L400", "NewModel", "new", "NewLoc", "2027-12-01", "NewPerson", "", ""],
        ["L401", "SameModel", "same", "SameLoc", "2027-01-01", "SamePerson", "", ""],
        ["L402", "NewFixture", "", "B-01", "2027-06-01", "NewOperator", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    records = svc.parse_import_file(str(filepath))
    results, summary = svc.precheck_import(records)
    assert summary[RESULT_NEW] == 1
    assert summary[RESULT_UPDATE] == 1
    assert summary[RESULT_SKIP] == 1
    assert summary[RESULT_ERROR] == 0
    for r in results:
        if r["fixture_no"] == "L400":
            assert r["result"] == RESULT_UPDATE
        elif r["fixture_no"] == "L401":
            assert r["result"] == RESULT_SKIP
        elif r["fixture_no"] == "L402":
            assert r["result"] == RESULT_NEW
    db.close()
    print("  PASS test_import_precheck_new_update_skip")


def test_import_precheck_scraped_protected():
    svc, db = make_service()
    fid = svc.add_fixture("L500", "ToScrap", "", "A-01", "2027-01-01", "张三")
    svc.scrap(fid, "管理员", "报废测试")

    out_dir = Path(TMP_DIR) / "precheck_scrap"
    out_dir.mkdir()
    rows = [
        ["L500", "OverwriteModel", "", "A-01", "2027-01-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    records = svc.parse_import_file(str(filepath))
    results, summary = svc.precheck_import(records)
    assert summary[RESULT_ERROR] == 1
    assert "已报废" in results[0]["errors"][0]
    db.close()
    print("  PASS test_import_precheck_scraped_protected")


def test_import_precheck_no_side_effects():
    svc, db = make_service()
    initial_count = len(svc.get_fixtures())

    out_dir = Path(TMP_DIR) / "precheck_side"
    out_dir.mkdir()
    rows = [
        ["L600", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
        ["L601", "LED", "", "A-02", "2027-01-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    records = svc.parse_import_file(str(filepath))
    results, summary = svc.precheck_import(records)

    assert summary[RESULT_NEW] == 2
    assert len(svc.get_fixtures()) == initial_count
    batches = svc.get_import_batches()
    assert len(batches) == 0

    after_count = db.execute("SELECT COUNT(*) as c FROM fixtures").fetchone()["c"]
    assert after_count == initial_count
    db.close()
    print("  PASS test_import_precheck_no_side_effects")


def test_import_execute_transaction_new():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "import_new"
    out_dir.mkdir()
    rows = [
        ["L700", "PAR64", "灯钩x1", "A-01", "2027-01-01", "张三", "可用", "批量导入测试"],
        ["L701", "LED200", "灯钩x1", "A-02", "2027-06-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员1", "test.csv")
    assert result["summary"]["new"] == 2
    assert result["summary"]["total"] == 2

    f1 = db.get_fixture_by_no("L700")
    f2 = db.get_fixture_by_no("L701")
    assert f1["model"] == "PAR64"
    assert f1["person_in_charge"] == "张三"
    assert f1["status"] == STATUS_AVAILABLE
    assert f2["status"] == STATUS_AVAILABLE

    batches = svc.get_import_batches()
    assert len(batches) == 1
    assert batches[0]["batch_no"] == result["batch_no"]
    assert batches[0]["operator"] == "导入员1"
    assert batches[0]["status"] == "completed"

    detail = svc.get_import_batch_detail(result["batch_id"])
    assert len(detail["items"]) == 2
    for item in detail["items"]:
        assert item["result"] == RESULT_NEW
        assert item["before_snapshot"] == ""
        assert item["after_snapshot"] != ""
        assert item["after_obj"]["fixture_no"] in ("L700", "L701")

    hist1 = db.get_history(f1["id"])
    assert any(h["action"] == "批量导入" for h in hist1)
    db.close()
    print("  PASS test_import_execute_transaction_new")


def test_import_execute_transaction_update():
    svc, db = make_service()
    fid = svc.add_fixture("L800", "OldModel", "old", "OldLoc", "2026-01-01", "OldPerson")

    out_dir = Path(TMP_DIR) / "import_update"
    out_dir.mkdir()
    rows = [
        ["L800", "NewModel", "new", "NewLoc", "2027-12-01", "NewPerson", "", "已更新"],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员2", "test.csv")
    assert result["summary"]["update"] == 1
    assert result["summary"]["new"] == 0

    f = db.get_fixture(fid)
    assert f["model"] == "NewModel"
    assert f["location"] == "NewLoc"
    assert f["inspection_due_date"] == "2027-12-01"
    assert f["person_in_charge"] == "NewPerson"
    assert f["status"] == STATUS_AVAILABLE

    detail = svc.get_import_batch_detail(result["batch_id"])
    item = detail["items"][0]
    assert item["result"] == RESULT_UPDATE
    before = item["before_obj"]
    after = item["after_obj"]
    assert before["model"] == "OldModel"
    assert after["model"] == "NewModel"

    hist = db.get_history(fid)
    assert any(h["action"] == "批量更新" for h in hist)
    db.close()
    print("  PASS test_import_execute_transaction_update")


def test_import_error_no_partial_import():
    svc, db = make_service()
    initial_count = len(svc.get_fixtures())
    out_dir = Path(TMP_DIR) / "import_atomic"
    out_dir.mkdir()
    rows = [
        ["L900", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
        ["L901", "", "", "A-02", "2027-01-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    try:
        svc.execute_import(str(filepath), "导入员3", "test.csv")
        assert False, "Should have raised ValueError for missing model"
    except ValueError as e:
        assert "预检发现错误" in str(e)
        assert hasattr(e, 'batch_id')

    after_count = len(svc.get_fixtures())
    assert after_count == initial_count
    f = db.get_fixture_by_no("L900")
    assert f is None

    batches = svc.get_import_batches()
    assert len(batches) == 1
    assert batches[0]["status"] == "failed"
    assert batches[0]["error_count"] >= 1
    db.close()
    print("  PASS test_import_error_no_partial_import")


def test_import_persistence_across_restart():
    db_path = Path(TMP_DIR) / "import_persist.db"
    db1 = DatabaseManager(db_path)
    svc1 = LightingService(db1)

    out_dir = Path(TMP_DIR) / "import_persist"
    out_dir.mkdir()
    rows = [
        ["LP001", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
        ["LP002", "LED", "", "A-02", "2027-06-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc1.execute_import(str(filepath), "导入员4", "persist.csv")
    batch_no = result["batch_no"]
    db1.close()

    db2 = DatabaseManager(db_path)
    svc2 = LightingService(db2)

    batches = svc2.get_import_batches()
    assert len(batches) == 1
    assert batches[0]["batch_no"] == batch_no
    assert batches[0]["status"] == "completed"

    detail = svc2.get_import_batch_detail(batches[0]["id"])
    assert len(detail["items"]) == 2
    assert detail["items"][0]["fixture_no"] == "LP001"

    f1 = db2.get_fixture_by_no("LP001")
    f2 = db2.get_fixture_by_no("LP002")
    assert f1 is not None
    assert f2 is not None
    db2.close()
    print("  PASS test_import_persistence_across_restart")


def test_rollback_new_fixtures():
    svc, db = make_service()
    initial_count = len(svc.get_fixtures())

    out_dir = Path(TMP_DIR) / "rollback_new"
    out_dir.mkdir()
    rows = [
        ["LR001", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
        ["LR002", "LED", "", "A-02", "2027-06-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员5", "test.csv")
    batch_id = result["batch_id"]

    assert len(svc.get_fixtures()) == initial_count + 2

    rb = svc.rollback_batch(batch_id, "回滚员1")
    assert rb["rolled_back"] == 2
    assert len(rb["conflicts"]) == 0

    assert len(svc.get_fixtures()) == initial_count
    assert db.get_fixture_by_no("LR001") is None
    assert db.get_fixture_by_no("LR002") is None

    batch = db.get_import_batch(batch_id)
    assert batch["status"] == "rolled_back"

    detail = svc.get_import_batch_detail(batch_id)
    for item in detail["items"]:
        assert item["result"] == RESULT_SKIP
        assert "已回滚" in item["error_message"]
    db.close()
    print("  PASS test_rollback_new_fixtures")


def test_rollback_update_fixtures():
    svc, db = make_service()
    fid = svc.add_fixture("LR100", "OldModel", "old", "OldLoc", "2026-01-01", "OldPerson")
    f_before = db.get_fixture(fid)

    out_dir = Path(TMP_DIR) / "rollback_update"
    out_dir.mkdir()
    rows = [
        ["LR100", "NewModel", "new", "NewLoc", "2027-12-01", "NewPerson", "", "updated"],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员6", "test.csv")
    batch_id = result["batch_id"]

    f_after = db.get_fixture(fid)
    assert f_after["model"] == "NewModel"

    rb = svc.rollback_batch(batch_id, "回滚员2")
    assert rb["rolled_back"] == 1

    f_final = db.get_fixture(fid)
    assert f_final["model"] == f_before["model"]
    assert f_final["location"] == f_before["location"]
    assert f_final["person_in_charge"] == f_before["person_in_charge"]
    assert f_final["inspection_due_date"] == f_before["inspection_due_date"]

    hist = db.get_history(fid)
    assert any(h["action"] == "批次回滚" for h in hist)
    db.close()
    print("  PASS test_rollback_update_fixtures")


def test_rollback_conflict_borrowed():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "rollback_conflict"
    out_dir.mkdir()
    rows = [
        ["LC001", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
        ["LC002", "LED", "", "A-02", "2027-06-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员7", "test.csv")
    batch_id = result["batch_id"]

    f1 = db.get_fixture_by_no("LC001")
    svc.borrow(f1["id"], "借用人", "演出借用")

    rb = svc.rollback_batch(batch_id, "回滚员3")
    assert rb["rolled_back"] == 1
    assert len(rb["conflicts"]) == 1
    assert rb["conflicts"][0]["fixture_no"] == "LC001"
    assert "借出" in rb["conflicts"][0]["reason"]

    assert db.get_fixture_by_no("LC001") is not None
    assert db.get_fixture_by_no("LC002") is None
    db.close()
    print("  PASS test_rollback_conflict_borrowed")


def test_rollback_conflict_all_items():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "rollback_all_conflict"
    out_dir.mkdir()
    rows = [
        ["LC100", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员8", "test.csv")
    batch_id = result["batch_id"]

    f = db.get_fixture_by_no("LC100")
    svc.borrow(f["id"], "借用人", "全部冲突测试")

    rb = svc.rollback_batch(batch_id, "回滚员4")
    assert rb["rolled_back"] == 0
    assert len(rb["conflicts"]) == 1
    assert rb["conflicts"][0]["fixture_no"] == "LC100"
    assert "借出" in rb["conflicts"][0]["reason"]

    assert db.get_fixture_by_no("LC100") is not None
    assert db.get_import_batch(batch_id)["status"] == "completed"
    db.close()
    print("  PASS test_rollback_conflict_all_items")


def test_rollback_conflict_inspection_freeze():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "rollback_freeze"
    out_dir.mkdir()
    rows = [
        ["LC200", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员9", "test.csv")
    batch_id = result["batch_id"]

    f = db.get_fixture_by_no("LC200")
    svc.freeze_inspection(f["id"], "巡检员", "到期巡检")

    rb = svc.rollback_batch(batch_id, "回滚员5")
    assert rb["rolled_back"] == 0
    assert len(rb["conflicts"]) == 1
    assert "巡检冻结" in rb["conflicts"][0]["reason"]

    f_after = db.get_fixture_by_no("LC200")
    assert f_after["status"] == STATUS_INSPECTION_FREEZE
    db.close()
    print("  PASS test_rollback_conflict_inspection_freeze")


def test_rollback_conflict_maintenance_freeze():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "rollback_maint"
    out_dir.mkdir()
    rows = [
        ["LC300", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员10", "test.csv")
    batch_id = result["batch_id"]

    f = db.get_fixture_by_no("LC300")
    svc.freeze_maintenance(f["id"], "维修员", "灯泡损坏")

    rb = svc.rollback_batch(batch_id, "回滚员6")
    assert rb["rolled_back"] == 0
    assert len(rb["conflicts"]) == 1
    assert "维修冻结" in rb["conflicts"][0]["reason"]
    db.close()
    print("  PASS test_rollback_conflict_maintenance_freeze")


def test_rollback_conflict_scrapped():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "rollback_scrap"
    out_dir.mkdir()
    rows = [
        ["LC400", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员11", "test.csv")
    batch_id = result["batch_id"]

    f = db.get_fixture_by_no("LC400")
    svc.scrap(f["id"], "管理员", "报废")

    rb = svc.rollback_batch(batch_id, "回滚员7")
    assert rb["rolled_back"] == 0
    assert len(rb["conflicts"]) == 1
    assert "报废" in rb["conflicts"][0]["reason"]
    db.close()
    print("  PASS test_rollback_conflict_scrapped")


def test_rollback_conflict_return_pending():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "rollback_return"
    out_dir.mkdir()
    rows = [
        ["LC500", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员12", "test.csv")
    batch_id = result["batch_id"]

    f = db.get_fixture_by_no("LC500")
    svc.borrow(f["id"], "借用人", "借用")
    svc.return_fixture(f["id"], "借用人", "归还")

    rb = svc.rollback_batch(batch_id, "回滚员8")
    assert rb["rolled_back"] == 0
    assert len(rb["conflicts"]) == 1
    assert "借出" in rb["conflicts"][0]["reason"] or "归还" in rb["conflicts"][0]["reason"]
    db.close()
    print("  PASS test_rollback_conflict_return_pending")


def test_export_batch_errors():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "export_errors"
    out_dir.mkdir()
    rows = [
        ["LE001", "PAR64", "", "A-01", "2027-01-01", "张三", "", ""],
        ["", "", "", "A-02", "2027-01-01", "李四", "", ""],
        ["LE003", "LED", "", "A-03", "bad-date", "王五", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    try:
        svc.execute_import(str(filepath), "test", "test.csv")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        batch_id = getattr(e, 'batch_id', None)
        assert batch_id is not None

    export_dir = Path(TMP_DIR) / "err_export"
    export_dir.mkdir()
    path = svc.export_batch_errors(batch_id, str(export_dir), "errors.csv")
    assert os.path.exists(path)
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows_out = list(reader)
    assert rows_out[0] == ["行号", "灯具编号", "结果", "错误信息"]
    error_nos = {r[1] for r in rows_out[1:]}
    assert "" in error_nos
    assert "LE003" in error_nos
    db.close()
    print("  PASS test_export_batch_errors")


def test_export_import_template():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "template_export"
    out_dir.mkdir()

    csv_path = svc.export_import_template(str(out_dir), "template.csv", fmt="csv")
    assert os.path.exists(csv_path)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    assert rows[0] == EXPORT_HEADERS
    assert len(rows) == 3

    json_path = svc.export_import_template(str(out_dir), "template.json", fmt="json")
    assert os.path.exists(json_path)
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["fixture_no"] == "L001"
    db.close()
    print("  PASS test_export_import_template")


def test_import_then_export_consistency():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "import_export"
    out_dir.mkdir()
    rows = [
        ["LX001", "PAR64", "灯钩x1", "A-01", "2027-01-01", "张三", "可用", "导入导出测试1"],
        ["LX002", "LED200", "灯钩x1,电源线x1", "A-02", "2027-06-01", "李四", "可用", "导入导出测试2"],
        ["LX003", "Beam 300", "灯钩x2", "A-03", "2027-12-01", "王五", "可用", "导入导出测试3"],
    ]
    filepath = _make_import_csv(out_dir, rows)
    svc.execute_import(str(filepath), "导入员13", "consistency.csv")

    fixtures = svc.get_fixtures()
    exported = out_dir / "exported.csv"
    svc.export_csv(fixtures, str(out_dir), "exported.csv")

    with open(exported, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        exported_rows = list(reader)

    for i, orig in enumerate(rows):
        exp = exported_rows[i]
        assert exp["灯具编号"] == orig[0]
        assert exp["型号"] == orig[1]
        assert exp["配件"] == orig[2]
        assert exp["库位"] == orig[3]
        assert exp["巡检到期日"] == orig[4]
        assert exp["负责人"] == orig[5]
        assert exp["状态"] == orig[6]
    db.close()
    print("  PASS test_import_then_export_consistency")


def test_import_mixed_new_update_skip_rollback_mixed():
    svc, db = make_service()
    svc.add_fixture("LM001", "OldModel", "old", "OldLoc", "2026-01-01", "OldPerson")
    svc.add_fixture("LM002", "SameModel", "same", "SameLoc", "2027-01-01", "SamePerson")
    initial_count = len(svc.get_fixtures())

    out_dir = Path(TMP_DIR) / "mixed_rollback"
    out_dir.mkdir()
    rows = [
        ["LM001", "NewModel", "new", "NewLoc", "2027-12-01", "NewPerson", "", ""],
        ["LM002", "SameModel", "same", "SameLoc", "2027-01-01", "SamePerson", "", ""],
        ["LM003", "BrandNew", "", "C-01", "2027-06-01", "NewGuy", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    result = svc.execute_import(str(filepath), "导入员14", "mixed.csv")
    batch_id = result["batch_id"]

    assert len(svc.get_fixtures()) == initial_count + 1
    f1 = db.get_fixture_by_no("LM001")
    assert f1["model"] == "NewModel"

    rb = svc.rollback_batch(batch_id, "回滚员9")
    assert rb["rolled_back"] == 2
    assert len(rb["conflicts"]) == 0

    assert len(svc.get_fixtures()) == initial_count
    f1_final = db.get_fixture_by_no("LM001")
    assert f1_final["model"] == "OldModel"
    assert db.get_fixture_by_no("LM003") is None
    db.close()
    print("  PASS test_import_mixed_new_update_skip_rollback_mixed")


def test_existing_lending_flow_not_broken():
    svc, db = make_service()
    fid = svc.add_fixture("FLOW001", "PAR64", "", "A-01", "2027-01-01", "张三")
    assert db.get_fixture(fid)["status"] == STATUS_AVAILABLE

    svc.borrow(fid, "王五", "演出借用")
    assert db.get_fixture(fid)["status"] == STATUS_BORROWED

    svc.return_fixture(fid, "王五", "归还完好")
    assert db.get_fixture(fid)["status"] == STATUS_RETURN_PENDING

    svc.review_return(fid, "李四", "复核通过")
    assert db.get_fixture(fid)["status"] == STATUS_AVAILABLE

    svc.freeze_inspection(fid, "巡检员", "到期")
    assert db.get_fixture(fid)["status"] == STATUS_INSPECTION_FREEZE
    svc.unfreeze_inspection(fid, "张三", "2027-12-31", "巡检完成")
    assert db.get_fixture(fid)["status"] == STATUS_AVAILABLE

    svc.freeze_maintenance(fid, "维修员", "损坏")
    assert db.get_fixture(fid)["status"] == STATUS_MAINTENANCE_FREEZE
    svc.unfreeze_maintenance(fid, "张三", "维修完成")
    assert db.get_fixture(fid)["status"] == STATUS_AVAILABLE

    hist = db.get_history(fid)
    actions = [h["action"] for h in hist]
    assert "借出" in actions
    assert "归还登记" in actions
    assert "复核入库" in actions
    assert "巡检冻结" in actions
    assert "巡检解冻" in actions
    assert "维修冻结" in actions
    assert "维修解冻" in actions
    db.close()
    print("  PASS test_existing_lending_flow_not_broken")


def test_failed_import_creates_batch_record():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "failed_batch"
    out_dir.mkdir()

    fid = svc.add_fixture("FS001", "OldModel", "", "A-01", "2027-01-01", "张三")
    svc.scrap(fid, "管理员", "报废测试")
    initial_count = len(svc.get_fixtures())

    rows = [
        ["", "PAR64", "", "A-01", "bad-date", "", "", ""],
        ["FS001", "NewModel", "", "A-01", "2027-01-01", "李四", "", ""],
        ["FS002", "LED", "", "A-02", "2027-01-01", "王五", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    try:
        svc.execute_import(str(filepath), "失败测试员", "failed.csv")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        batch_id = getattr(e, 'batch_id', None)
        batch_no = getattr(e, 'batch_no', None)
        assert batch_id is not None
        assert batch_no is not None
        assert "预检发现错误" in str(e)
        assert batch_no in str(e)

    assert len(svc.get_fixtures()) == initial_count
    assert db.get_fixture_by_no("FS002") is None

    batches = svc.get_import_batches()
    assert len(batches) == 1
    assert batches[0]["status"] == "failed"
    assert batches[0]["operator"] == "失败测试员"
    assert batches[0]["source_file"] == "failed.csv"
    assert batches[0]["error_count"] >= 2

    detail = svc.get_import_batch_detail(batches[0]["id"])
    error_items = [it for it in detail["items"] if it["result"] == RESULT_ERROR]
    assert len(error_items) >= 2
    error_msgs = "; ".join(it["error_message"] for it in error_items)
    assert "灯具编号不能为空" in error_msgs or "缺少必填字段" in error_msgs
    assert "已报废" in error_msgs or "日期格式错误" in error_msgs

    try:
        svc.rollback_batch(batches[0]["id"], "回滚员")
        assert False, "Should not allow rollback of failed batch"
    except ValueError as e:
        assert "无法回滚" in str(e)

    db.close()
    print("  PASS test_failed_import_creates_batch_record")


def test_failed_import_persistence_across_restart():
    db_path = Path(TMP_DIR) / "failed_persist.db"
    db1 = DatabaseManager(db_path)
    svc1 = LightingService(db1)

    out_dir = Path(TMP_DIR) / "failed_persist_dir"
    out_dir.mkdir()
    rows = [
        ["FP001", "", "", "A-01", "bad-date", "张三", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    batch_id = None
    batch_no = None
    try:
        svc1.execute_import(str(filepath), "重启测试员", "restart.csv")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        batch_id = getattr(e, 'batch_id', None)
        batch_no = getattr(e, 'batch_no', None)
    db1.close()

    db2 = DatabaseManager(db_path)
    svc2 = LightingService(db2)

    batches = svc2.get_import_batches()
    assert len(batches) == 1
    assert batches[0]["batch_no"] == batch_no
    assert batches[0]["status"] == "failed"
    assert batches[0]["operator"] == "重启测试员"

    detail = svc2.get_import_batch_detail(batches[0]["id"])
    error_items = [it for it in detail["items"] if it["result"] == RESULT_ERROR]
    assert len(error_items) >= 1

    export_dir = Path(TMP_DIR) / "failed_persist_export"
    export_dir.mkdir()
    path = svc2.export_batch_errors(batches[0]["id"], str(export_dir), "errors_after_restart.csv")
    assert os.path.exists(path)
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows_out = list(reader)
    assert rows_out[0] == ["行号", "灯具编号", "结果", "错误信息"]
    assert len(rows_out) > 1

    db2.close()
    print("  PASS test_failed_import_persistence_across_restart")


def test_failed_import_error_export_matches_precheck():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "err_match"
    out_dir.mkdir()

    fid = svc.add_fixture("EM001", "Model", "", "A-01", "2027-01-01", "张三")
    svc.scrap(fid, "管理员", "报废")

    rows = [
        ["", "PAR64", "", "A-01", "bad-date", "", "无效状态", ""],
        ["EM001", "NewModel", "", "A-01", "2027-01-01", "李四", "", ""],
    ]
    filepath = _make_import_csv(out_dir, rows)
    records = svc.parse_import_file(str(filepath))
    precheck_results, precheck_summary = svc.precheck_import(records)

    batch_id = None
    try:
        svc.execute_import(str(filepath), "匹配测试员", "match.csv")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        batch_id = getattr(e, 'batch_id', None)

    export_dir = Path(TMP_DIR) / "err_match_export"
    export_dir.mkdir()
    path = svc.export_batch_errors(batch_id, str(export_dir), "match_errors.csv")
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        exported = list(reader)

    error_rows_precheck = [r for r in precheck_results if r["result"] == RESULT_ERROR]
    assert len(exported) - 1 == len(error_rows_precheck)

    exported_info = {}
    for row in exported[1:]:
        exported_info[row[1]] = row[3]

    for r in error_rows_precheck:
        fno = r["fixture_no"]
        assert fno in exported_info, f"Fixture {fno} missing from export"
        for err in r["errors"]:
            assert err in exported_info[fno] or any(
                kw in exported_info[fno] for kw in err.split(":")
            ), f"Precheck error '{err}' not in export '{exported_info[fno]}'"

    db.close()
    print("  PASS test_failed_import_error_export_matches_precheck")


def test_successful_import_unaffected_by_failed_batch():
    svc, db = make_service()
    out_dir = Path(TMP_DIR) / "success_after_fail"
    out_dir.mkdir()

    fail_dir = out_dir / "fail"
    fail_dir.mkdir()
    fail_rows = [
        ["SF001", "", "", "A-01", "2027-01-01", "张三", "", ""],
    ]
    fail_filepath = _make_import_csv(fail_dir, fail_rows)
    try:
        svc.execute_import(str(fail_filepath), "失败操作员", "fail.csv")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    fail_batches = [b for b in svc.get_import_batches() if b["status"] == "failed"]
    assert len(fail_batches) == 1

    success_dir = out_dir / "success"
    success_dir.mkdir()
    success_rows = [
        ["SF002", "PAR64", "", "A-02", "2027-01-01", "李四", "", ""],
        ["SF003", "LED", "", "A-03", "2027-06-01", "王五", "", ""],
    ]
    success_filepath = _make_import_csv(success_dir, success_rows)
    result = svc.execute_import(str(success_filepath), "成功操作员", "success.csv")
    assert result["summary"]["new"] == 2
    assert result["summary"]["error"] == 0

    all_batches = svc.get_import_batches()
    assert len(all_batches) == 2
    completed_batches = [b for b in all_batches if b["status"] == "completed"]
    assert len(completed_batches) == 1
    assert completed_batches[0]["operator"] == "成功操作员"

    f2 = db.get_fixture_by_no("SF002")
    f3 = db.get_fixture_by_no("SF003")
    assert f2 is not None
    assert f3 is not None
    assert f2["model"] == "PAR64"
    assert f3["model"] == "LED"
    db.close()
    print("  PASS test_successful_import_unaffected_by_failed_batch")


def main():
    setup()
    tests = [
        test_add_fixture,
        test_add_duplicate_fixture,
        test_borrow_flow,
        test_borrow_inspection_expired,
        test_return_and_review,
        test_duplicate_return,
        test_inspection_freeze_unfreeze,
        test_unfreeze_inspection_wrong_person,
        test_maintenance_freeze_unfreeze,
        test_unfreeze_maintenance_wrong_person,
        test_scrap,
        test_invalid_transition,
        test_filter_fixtures,
        test_export_csv,
        test_export_json,
        test_export_unwritable_dir,
        test_persistence_across_restart,
        test_history_timeline,
        test_status_counts,
        test_export_matches_filter,
        test_borrow_from_borrowed_rejected,
        test_freeze_from_borrowed,
        test_update_fixture_info,
        test_filter_persistence_rootcause_empty_on_start,
        test_filter_location_status_kept_across_restart,
        test_filter_due_range_effective_across_restart,
        test_export_uses_restored_filter_results,
        test_reset_clears_persisted_filters,
        test_import_parse_csv,
        test_import_parse_json,
        test_import_precheck_validation_errors,
        test_import_precheck_new_update_skip,
        test_import_precheck_scraped_protected,
        test_import_precheck_no_side_effects,
        test_import_execute_transaction_new,
        test_import_execute_transaction_update,
        test_import_error_no_partial_import,
        test_import_persistence_across_restart,
        test_rollback_new_fixtures,
        test_rollback_update_fixtures,
        test_rollback_conflict_borrowed,
        test_rollback_conflict_all_items,
        test_rollback_conflict_inspection_freeze,
        test_rollback_conflict_maintenance_freeze,
        test_rollback_conflict_scrapped,
        test_rollback_conflict_return_pending,
        test_export_batch_errors,
        test_export_import_template,
        test_import_then_export_consistency,
        test_import_mixed_new_update_skip_rollback_mixed,
        test_existing_lending_flow_not_broken,
        test_failed_import_creates_batch_record,
        test_failed_import_persistence_across_restart,
        test_failed_import_error_export_matches_precheck,
        test_successful_import_unaffected_by_failed_batch,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    teardown()
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
    if failed > 0:
        sys.exit(1)
    else:
        print("All tests passed!")


if __name__ == "__main__":
    main()
