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
