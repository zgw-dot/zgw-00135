#!/usr/bin/env python3
import unittest
import os
import sqlite3
import json
import csv
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

sys_path = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, sys_path)

from stage_lighting_tool import DatabaseManager, LightingService, RESULT_NEW, RESULT_UPDATE, RESULT_SKIP, RESULT_ERROR


class TestDraftHandover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_handover.db")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.db = DatabaseManager(self.db_path)
        self.service = LightingService(self.db)

    def tearDown(self):
        self.db.close()

    def _create_test_draft(self, operator="张三"):
        precheck_results = [
            {
                "row": 2,
                "fixture_no": "T001",
                "result": RESULT_NEW,
                "errors": [],
                "warnings": [],
                "before": None,
                "after": {"model": "LED200", "accessories": "灯钩x1", "location": "A-01",
                          "inspection_due_date": "2027-12-31", "person_in_charge": operator,
                          "status": "可用", "last_remark": ""},
                "record": {"fixture_no": "T001", "model": "LED200", "person_in_charge": operator},
            },
        ]
        summary = {"total": 1, RESULT_NEW: 1, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_import.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", operator])
        result = self.service.create_draft_from_precheck(tmp_csv, operator, precheck_results, summary)
        return result["draft_id"]

    def test_draft_creator_is_default_operator(self):
        draft_id = self._create_test_draft("张三")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["operator"], "张三")
        self.assertEqual(draft.get("current_operator") or draft["operator"], "张三")

    def test_check_draft_lock_owner(self):
        draft_id = self._create_test_draft("张三")
        lock = self.service.check_draft_lock(draft_id, "张三")
        self.assertTrue(lock["is_owner"])
        self.assertEqual(lock["owner"], "张三")
        self.assertEqual(lock["creator"], "张三")

    def test_check_draft_lock_non_owner(self):
        draft_id = self._create_test_draft("张三")
        lock = self.service.check_draft_lock(draft_id, "李四")
        self.assertFalse(lock["is_owner"])
        self.assertEqual(lock["owner"], "张三")

    def test_takeover_draft(self):
        draft_id = self._create_test_draft("张三")
        result = self.service.takeover_draft(draft_id, "李四", "张三请假，我来接手")
        self.assertEqual(result["from_operator"], "张三")
        self.assertEqual(result["to_operator"], "李四")

        lock = self.service.check_draft_lock(draft_id, "李四")
        self.assertTrue(lock["is_owner"])

        lock_zhang = self.service.check_draft_lock(draft_id, "张三")
        self.assertFalse(lock_zhang["is_owner"])

    def test_takeover_requires_reason(self):
        draft_id = self._create_test_draft("张三")
        with self.assertRaises(ValueError) as ctx:
            self.service.takeover_draft(draft_id, "李四", "")
        self.assertIn("交接原因", str(ctx.exception))

    def test_takeover_same_operator_fails(self):
        draft_id = self._create_test_draft("张三")
        with self.assertRaises(ValueError) as ctx:
            self.service.takeover_draft(draft_id, "张三", "测试")
        self.assertIn("已经是当前操作人", str(ctx.exception))

    def test_submit_by_non_owner_fails(self):
        draft_id = self._create_test_draft("张三")
        with self.assertRaises(ValueError) as ctx:
            self.service.submit_draft(draft_id, "李四")
        self.assertIn("只有操作人才能提交", str(ctx.exception))

    def test_takeover_and_submit(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "张三出差，我来处理")

        result = self.service.submit_draft(draft_id, "李四")
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["new"], 1)

    def test_cross_restart_lock_persistence(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "测试跨重启")

        db_path = self.db_path
        self.db.close()

        db2 = DatabaseManager(db_path)
        svc2 = LightingService(db2)

        draft = db2.get_draft_batch(draft_id)
        self.assertEqual(draft.get("current_operator"), "李四")

        lock = svc2.check_draft_lock(draft_id, "张三")
        self.assertFalse(lock["is_owner"])

        lock_li = svc2.check_draft_lock(draft_id, "李四")
        self.assertTrue(lock_li["is_owner"])

        db2.close()

        self.db = DatabaseManager(db_path)
        self.service = LightingService(self.db)

    def test_handover_log_recorded(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "第一次交接")
        self.service.takeover_draft(draft_id, "王五", "第二次交接")

        log = self.service.get_draft_handover_log(draft_id)
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0]["from_operator"], "张三")
        self.assertEqual(log[0]["to_operator"], "李四")
        self.assertEqual(log[0]["reason"], "第一次交接")
        self.assertEqual(log[1]["from_operator"], "李四")
        self.assertEqual(log[1]["to_operator"], "王五")
        self.assertEqual(log[1]["reason"], "第二次交接")

    def test_handover_log_consistency_with_draft(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接A")

        draft = self.db.get_draft_batch(draft_id)
        log = self.service.get_draft_handover_log(draft_id)

        self.assertEqual(draft["current_operator"], "李四")
        self.assertEqual(draft["handover_reason"], "交接A")
        self.assertTrue(draft["handover_at"])
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["to_operator"], "李四")
        self.assertEqual(log[0]["reason"], "交接A")

    def test_export_draft_items_includes_handover(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "测试导出")

        export_dir = os.path.join(self.tmpdir, "export_test")
        os.makedirs(export_dir, exist_ok=True)
        path = self.service.export_draft_items(draft_id, export_dir, "test_items.csv")

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
        self.assertIn("创建人", headers)
        self.assertIn("最近接手人", headers)
        self.assertIn("交接备注", headers)

        idx_creator = headers.index("创建人")
        idx_current = headers.index("最近接手人")
        idx_reason = headers.index("交接备注")

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            row = next(reader)
        self.assertEqual(row[idx_creator], "张三")
        self.assertEqual(row[idx_current], "李四")
        self.assertEqual(row[idx_reason], "测试导出")

    def test_export_draft_conflicts_includes_handover(self):
        self.db.add_fixture("C001", "PAR64", "", "A-01", "2027-12-31", "张三")
        precheck_results = [
            {
                "row": 2,
                "fixture_no": "C001",
                "result": RESULT_UPDATE,
                "errors": [],
                "warnings": [],
                "before": {"id": 1, "model": "PAR64", "accessories": "", "location": "A-01",
                           "inspection_due_date": "2027-12-31", "person_in_charge": "张三",
                           "status": "可用", "last_remark": ""},
                "after": {"model": "LED300", "accessories": "", "location": "A-01",
                          "inspection_due_date": "2027-12-31", "person_in_charge": "张三",
                          "status": "可用", "last_remark": ""},
                "record": {"fixture_no": "C001", "model": "LED300", "person_in_charge": "张三"},
            },
        ]
        summary = {"total": 1, RESULT_NEW: 0, RESULT_UPDATE: 1, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_conflict.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["C001", "LED300", "张三"])
        result = self.service.create_draft_from_precheck(tmp_csv, "张三", precheck_results, summary)
        draft_id = result["draft_id"]

        self.service.takeover_draft(draft_id, "李四", "冲突导出测试")
        self.service.detect_and_persist_conflicts(draft_id, tmp_csv)

        export_dir = os.path.join(self.tmpdir, "export_conflict_test")
        os.makedirs(export_dir, exist_ok=True)
        path = self.service.export_draft_conflicts(draft_id, export_dir, "test_conflicts.csv")

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
        self.assertIn("创建人", headers)
        self.assertIn("最近接手人", headers)
        self.assertIn("交接备注", headers)

    def test_original_decisions_preserved_after_takeover(self):
        self.db.add_fixture("D001", "PAR64", "", "A-01", "2027-12-31", "张三")
        precheck_results = [
            {
                "row": 2,
                "fixture_no": "D001",
                "result": RESULT_UPDATE,
                "errors": [],
                "warnings": [],
                "before": {"id": 1, "model": "PAR64", "accessories": "", "location": "A-01",
                           "inspection_due_date": "2027-12-31", "person_in_charge": "张三",
                           "status": "可用", "last_remark": ""},
                "after": {"model": "LED300", "accessories": "", "location": "A-01",
                          "inspection_due_date": "2027-12-31", "person_in_charge": "张三",
                          "status": "可用", "last_remark": ""},
                "record": {"fixture_no": "D001", "model": "LED300", "person_in_charge": "张三"},
            },
        ]
        summary = {"total": 1, RESULT_NEW: 0, RESULT_UPDATE: 1, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_preserve.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["D001", "LED300", "张三"])
        result = self.service.create_draft_from_precheck(tmp_csv, "张三", precheck_results, summary)
        draft_id = result["draft_id"]

        fixture = self.db.get_fixture_by_no("D001")
        self.db.update_fixture(fixture["id"], model="CHANGED_MODEL")

        self.service.detect_and_persist_conflicts(draft_id, tmp_csv)

        items = self.db.get_draft_items(draft_id)
        conflict_item = None
        for it in items:
            if it["conflict_status"]:
                conflict_item = it
                break
        self.assertIsNotNone(conflict_item)

        self.service.keep_original_draft_item(conflict_item["id"], "张三决定保持原样")

        self.service.takeover_draft(draft_id, "李四", "接手处理")

        items_after = self.db.get_draft_items(draft_id)
        same_item = [it for it in items_after if it["id"] == conflict_item["id"]][0]
        self.assertEqual(same_item["resolution_action"], "original")
        self.assertEqual(same_item["resolution_note"], "张三决定保持原样")

    def test_takeover_on_submitted_draft_fails(self):
        draft_id = self._create_test_draft("张三")
        self.service.submit_draft(draft_id, "张三")
        with self.assertRaises(ValueError) as ctx:
            self.service.takeover_draft(draft_id, "李四", "想接手")
        self.assertIn("无法接手", str(ctx.exception))

    def test_batch_final_detail_includes_handover(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "最终明细测试")
        result = self.service.submit_draft(draft_id, "李四")
        batch_id = result["batch_id"]

        export_dir = os.path.join(self.tmpdir, "export_batch_test")
        os.makedirs(export_dir, exist_ok=True)
        path = self.service.export_batch_final_detail(batch_id, export_dir, "test_final.csv")

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
        self.assertIn("创建人", headers)
        self.assertIn("最近接手人", headers)
        self.assertIn("交接备注", headers)

        idx_creator = headers.index("创建人")
        idx_current = headers.index("最近接手人")
        idx_reason = headers.index("交接备注")

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            row = next(reader)
        self.assertEqual(row[idx_creator], "张三")
        self.assertEqual(row[idx_current], "李四")
        self.assertEqual(row[idx_reason], "最终明细测试")


class TestDraftHandoverDBSchema(unittest.TestCase):
    def test_handover_log_table_exists(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "schema_test.db")
        try:
            db = DatabaseManager(db_path)
            row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='draft_handover_log'").fetchone()
            self.assertIsNotNone(row)
            db.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_draft_batch_new_columns(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "col_test.db")
        try:
            db = DatabaseManager(db_path)
            cols = [r[1] for r in db.execute("PRAGMA table_info(import_draft_batches)").fetchall()]
            self.assertIn("current_operator", cols)
            self.assertIn("handover_reason", cols)
            self.assertIn("handover_at", cols)
            db.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
