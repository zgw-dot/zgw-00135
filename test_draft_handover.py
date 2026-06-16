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

from stage_lighting_tool import (
    DatabaseManager, LightingService,
    RESULT_NEW, RESULT_UPDATE, RESULT_SKIP, RESULT_ERROR,
    DRAFT_STATUS_DRAFT, DRAFT_STATUS_SUBMITTED, DRAFT_STATUS_PARTIAL,
    DRAFT_STATUS_CONFLICT_SUBMITTED, DRAFT_STATUS_DELETED,
    DRAFT_STATUS_DISPLAY,
    DRAFT_PERM_EDIT, DRAFT_PERM_SUBMIT, DRAFT_PERM_ASSIGN,
    DRAFT_PERM_WITHDRAW, DRAFT_PERM_CREATE, DRAFT_PERM_READONLY,
    DRAFT_OPERATION_CREATE, DRAFT_OPERATION_ASSIGN, DRAFT_OPERATION_TAKEOVER,
    DRAFT_OPERATION_SUBMIT, DRAFT_OPERATION_EXPORT, DRAFT_OPERATION_EDIT_REMARK,
    DRAFT_OPERATION_CONFLICT_RESOLVE, DRAFT_OPERATION_WITHDRAW_TAKEOVER,
    DRAFT_OPERATION_RELEASE,
    HANDOVER_TYPE_ASSIGN, HANDOVER_TYPE_TAKEOVER, HANDOVER_TYPE_WITHDRAW,
    HANDOVER_TYPE_RELEASE,
    CONFLICT_STATUS_PENDING, CONFLICT_STATUS_RESOLVED, CONFLICT_STATUS_DISCARDED,
    CONFLICT_STATUS_NONE,
    RESOLUTION_ACTION_ORIGINAL, RESOLUTION_ACTION_REFRESH, RESOLUTION_ACTION_DISCARD,
)


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

    def _create_conflict_draft(self, operator="张三"):
        self.db.add_fixture("C001", "PAR64", "", "A-01", "2027-12-31", operator)
        precheck_results = [
            {
                "row": 2,
                "fixture_no": "C001",
                "result": RESULT_UPDATE,
                "errors": [],
                "warnings": [],
                "before": {"id": 1, "model": "PAR64", "accessories": "", "location": "A-01",
                           "inspection_due_date": "2027-12-31", "person_in_charge": operator,
                           "status": "可用", "last_remark": ""},
                "after": {"model": "LED300", "accessories": "", "location": "A-01",
                          "inspection_due_date": "2027-12-31", "person_in_charge": operator,
                          "status": "可用", "last_remark": ""},
                "record": {"fixture_no": "C001", "model": "LED300", "person_in_charge": operator},
            },
        ]
        summary = {"total": 1, RESULT_NEW: 0, RESULT_UPDATE: 1, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_conflict.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["C001", "LED300", operator])
        result = self.service.create_draft_from_precheck(tmp_csv, operator, precheck_results, summary)
        draft_id = result["draft_id"]
        fixture = self.db.get_fixture_by_no("C001")
        self.db.update_fixture(fixture["id"], model="CHANGED_MODEL")
        self.service.detect_and_persist_conflicts(draft_id, tmp_csv)
        return draft_id

    def test_draft_creator_is_default_operator(self):
        draft_id = self._create_test_draft("张三")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["operator"], "张三")
        self.assertEqual(draft.get("current_operator") or draft["operator"], "张三")

    def test_check_draft_lock_owner(self):
        draft_id = self._create_test_draft("张三")
        lock = self.service.check_draft_lock(draft_id, "张三")
        self.assertTrue(lock["is_owner"])

    def test_check_draft_lock_non_owner(self):
        draft_id = self._create_test_draft("张三")
        lock = self.service.check_draft_lock(draft_id, "李四")
        self.assertFalse(lock["is_owner"])

    def test_takeover_draft(self):
        draft_id = self._create_test_draft("张三")
        result = self.service.takeover_draft(draft_id, "李四", "张三请假")
        self.assertEqual(result["from_operator"], "张三")
        self.assertEqual(result["to_operator"], "李四")
        lock = self.service.check_draft_lock(draft_id, "李四")
        self.assertTrue(lock["is_owner"])

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

    def test_submit_by_non_owner_non_creator_fails(self):
        draft_id = self._create_test_draft("张三")
        with self.assertRaises(ValueError) as ctx:
            self.service.submit_draft(draft_id, "李四")
        self.assertIn("可以提交", str(ctx.exception))

    def test_submit_by_creator_even_if_not_current_operator(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "张三出差")
        result = self.service.submit_draft(draft_id, "张三")
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["new"], 1)

    def test_submit_by_assignee(self):
        draft_id = self._create_test_draft("张三")
        self.service.assign_draft(draft_id, "张三", "王五", "请王五处理")
        result = self.service.submit_draft(draft_id, "王五")
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["new"], 1)

    def test_takeover_and_submit(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "张三出差")
        result = self.service.submit_draft(draft_id, "李四")
        self.assertTrue(result["success"])

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

    def test_handover_log_consistency_with_draft(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接A")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["current_operator"], "李四")
        self.assertEqual(draft["handover_reason"], "交接A")

    def test_export_draft_items_includes_handover(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "测试导出")
        export_dir = os.path.join(self.tmpdir, "export_test")
        os.makedirs(export_dir, exist_ok=True)
        path = self.service.export_draft_items(draft_id, export_dir, "test_items.csv", operator="张三")
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            row = next(reader)
        self.assertIn("创建人", headers)
        self.assertIn("最近接手人", headers)
        self.assertIn("交接备注", headers)
        idx_creator = headers.index("创建人")
        idx_current = headers.index("最近接手人")
        idx_reason = headers.index("交接备注")
        self.assertEqual(row[idx_creator], "张三")
        self.assertEqual(row[idx_current], "李四")
        self.assertEqual(row[idx_reason], "测试导出")

    def test_export_draft_conflicts_includes_handover(self):
        draft_id = self._create_conflict_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "冲突导出测试")
        export_dir = os.path.join(self.tmpdir, "export_conflict_test")
        os.makedirs(export_dir, exist_ok=True)
        path = self.service.export_draft_conflicts(draft_id, export_dir, "test_conflicts.csv", operator="李四")
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
        self.assertIn("最近接手人", headers)
        self.assertIn("交接备注", headers)

    def test_original_decisions_preserved_after_takeover(self):
        draft_id = self._create_conflict_draft("张三")
        items = self.db.get_draft_items(draft_id)
        conflict_item = [it for it in items if it["conflict_status"]][0]
        self.service.keep_original_draft_item(conflict_item["id"], "张三决定保持原样", operator="张三")
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
            row = next(reader)
        self.assertIn("创建人", headers)
        self.assertIn("最近接手人", headers)
        self.assertIn("交接备注", headers)

    def test_assign_draft(self):
        draft_id = self._create_test_draft("张三")
        result = self.service.assign_draft(draft_id, "张三", "王五", "请王五处理")
        self.assertEqual(result["to_operator"], "王五")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["assigned_to"], "王五")
        self.assertEqual(draft["assigned_by"], "张三")

    def test_assign_by_non_creator_fails(self):
        draft_id = self._create_test_draft("张三")
        with self.assertRaises(ValueError) as ctx:
            self.service.assign_draft(draft_id, "李四", "王五", "擅自分派")
        self.assertIn("才能分派", str(ctx.exception))

    def test_withdraw_takeover(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接测试")
        result = self.service.withdraw_takeover(draft_id, "张三", "撤回测试")
        self.assertEqual(result["to_operator"], "张三")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["current_operator"], "张三")

    def test_release_draft(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接测试")
        result = self.service.release_draft(draft_id, "李四", "释放测试")
        self.assertEqual(result["to_operator"], "张三")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["current_operator"], "张三")


class TestDraftPermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_perms.db")

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

    def test_anonymous_user_is_readonly(self):
        draft_id = self._create_test_draft("张三")
        perms = self.service.check_draft_permissions(draft_id, "")
        self.assertTrue(perms["is_readonly"])
        self.assertIn(DRAFT_PERM_READONLY, perms["permissions"])
        self.assertNotIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertNotIn(DRAFT_PERM_SUBMIT, perms["permissions"])

    def test_none_user_is_readonly(self):
        draft_id = self._create_test_draft("张三")
        perms = self.service.check_draft_permissions(draft_id, None)
        self.assertTrue(perms["is_readonly"])

    def test_creator_can_edit_and_submit(self):
        draft_id = self._create_test_draft("张三")
        perms = self.service.check_draft_permissions(draft_id, "张三")
        self.assertFalse(perms["is_readonly"])
        self.assertTrue(perms["is_creator"])
        self.assertIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertIn(DRAFT_PERM_SUBMIT, perms["permissions"])
        self.assertIn(DRAFT_PERM_ASSIGN, perms["permissions"])

    def test_creator_can_edit_after_takeover(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接")
        perms = self.service.check_draft_permissions(draft_id, "张三")
        self.assertFalse(perms["is_readonly"])
        self.assertIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertIn(DRAFT_PERM_SUBMIT, perms["permissions"])

    def test_current_operator_can_edit_and_submit(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接")
        perms = self.service.check_draft_permissions(draft_id, "李四")
        self.assertFalse(perms["is_readonly"])
        self.assertTrue(perms["is_owner"])
        self.assertIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertIn(DRAFT_PERM_SUBMIT, perms["permissions"])

    def test_assigned_person_can_edit_and_submit(self):
        draft_id = self._create_test_draft("张三")
        self.service.assign_draft(draft_id, "张三", "王五", "分派测试")
        perms = self.service.check_draft_permissions(draft_id, "王五")
        self.assertFalse(perms["is_readonly"])
        self.assertTrue(perms["is_assigned"])
        self.assertIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertIn(DRAFT_PERM_SUBMIT, perms["permissions"])

    def test_unrelated_person_is_readonly(self):
        draft_id = self._create_test_draft("张三")
        perms = self.service.check_draft_permissions(draft_id, "赵六")
        self.assertTrue(perms["is_readonly"])
        self.assertNotIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertNotIn(DRAFT_PERM_SUBMIT, perms["permissions"])

    def test_submitted_draft_no_edit_no_submit(self):
        draft_id = self._create_test_draft("张三")
        self.service.submit_draft(draft_id, "张三")
        perms = self.service.check_draft_permissions(draft_id, "张三")
        self.assertTrue(perms["is_readonly"])
        self.assertNotIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertNotIn(DRAFT_PERM_SUBMIT, perms["permissions"])

    def test_partial_submit_draft_still_editable(self):
        precheck_results = [
            {"row": 2, "fixture_no": "T001", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "LED200", "person_in_charge": "张三", "status": "可用"},
             "record": {"fixture_no": "T001", "model": "LED200", "person_in_charge": "张三"}},
            {"row": 3, "fixture_no": "T002", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "PAR64", "person_in_charge": "张三", "status": "可用"},
             "record": {"fixture_no": "T002", "model": "PAR64", "person_in_charge": "张三"}},
        ]
        summary = {"total": 2, RESULT_NEW: 2, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_partial.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", "张三"])
            w.writerow(["T002", "PAR64", "张三"])
        result = self.service.create_draft_from_precheck(tmp_csv, "张三", precheck_results, summary)
        draft_id = result["draft_id"]
        self.service.set_draft_item_selected(
            self.db.get_draft_items(draft_id)[0]["id"], False
        )
        self.service.submit_draft(draft_id, "张三")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["status"], DRAFT_STATUS_PARTIAL)
        perms = self.service.check_draft_permissions(draft_id, "张三")
        self.assertFalse(perms["is_readonly"])
        self.assertIn(DRAFT_PERM_EDIT, perms["permissions"])
        self.assertIn(DRAFT_PERM_SUBMIT, perms["permissions"])


class TestDraftOperationLog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_oplog.db")

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
            {"row": 2, "fixture_no": "T001", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "LED200", "person_in_charge": operator, "status": "可用"},
             "record": {"fixture_no": "T001", "model": "LED200", "person_in_charge": operator}},
        ]
        summary = {"total": 1, RESULT_NEW: 1, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_import.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", operator])
        result = self.service.create_draft_from_precheck(tmp_csv, operator, precheck_results, summary)
        return result["draft_id"]

    def test_create_draft_logs_operation(self):
        draft_id = self._create_test_draft("张三")
        log = self.service.get_draft_operation_log(draft_id)
        create_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_CREATE]
        self.assertEqual(len(create_ops), 1)
        self.assertEqual(create_ops[0]["operator"], "张三")
        self.assertIn("导入创建", create_ops[0]["detail"])

    def test_takeover_logs_operation(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接测试")
        log = self.service.get_draft_operation_log(draft_id)
        takeover_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_TAKEOVER]
        self.assertEqual(len(takeover_ops), 1)
        self.assertEqual(takeover_ops[0]["operator"], "李四")
        self.assertEqual(takeover_ops[0]["detail"], "交接测试")

    def test_assign_logs_operation(self):
        draft_id = self._create_test_draft("张三")
        self.service.assign_draft(draft_id, "张三", "王五", "分派测试")
        log = self.service.get_draft_operation_log(draft_id)
        assign_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_ASSIGN]
        self.assertEqual(len(assign_ops), 1)
        self.assertEqual(assign_ops[0]["operator"], "张三")
        self.assertIn("分派给王五", assign_ops[0]["detail"])

    def test_submit_logs_operation(self):
        draft_id = self._create_test_draft("张三")
        self.service.submit_draft(draft_id, "张三")
        log = self.service.get_draft_operation_log(draft_id)
        submit_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_SUBMIT]
        self.assertEqual(len(submit_ops), 1)
        self.assertEqual(submit_ops[0]["operator"], "张三")
        self.assertIn("提交批次", submit_ops[0]["detail"])

    def test_export_logs_operation(self):
        draft_id = self._create_test_draft("张三")
        export_dir = os.path.join(self.tmpdir, "export_log_test")
        os.makedirs(export_dir, exist_ok=True)
        self.service.export_draft_items(draft_id, export_dir, "test.csv", operator="张三")
        log = self.service.get_draft_operation_log(draft_id)
        export_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_EXPORT]
        self.assertEqual(len(export_ops), 1)
        self.assertEqual(export_ops[0]["operator"], "张三")

    def test_edit_remark_logs_operation(self):
        draft_id = self._create_test_draft("张三")
        self.service.update_draft_remark(draft_id, "新备注", operator="张三")
        log = self.service.get_draft_operation_log(draft_id)
        remark_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_EDIT_REMARK]
        self.assertEqual(len(remark_ops), 1)
        self.assertEqual(remark_ops[0]["operator"], "张三")

    def test_conflict_resolve_logs_operation(self):
        self.db.add_fixture("C001", "PAR64", "", "A-01", "2027-12-31", "张三")
        precheck_results = [
            {"row": 2, "fixture_no": "C001", "result": RESULT_UPDATE, "errors": [], "warnings": [],
             "before": {"id": 1, "model": "PAR64", "person_in_charge": "张三", "status": "可用"},
             "after": {"model": "LED300", "person_in_charge": "张三", "status": "可用"},
             "record": {"fixture_no": "C001", "model": "LED300", "person_in_charge": "张三"}},
        ]
        summary = {"total": 1, RESULT_NEW: 0, RESULT_UPDATE: 1, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_conflict.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["C001", "LED300", "张三"])
        result = self.service.create_draft_from_precheck(tmp_csv, "张三", precheck_results, summary)
        draft_id = result["draft_id"]
        fixture = self.db.get_fixture_by_no("C001")
        self.db.update_fixture(fixture["id"], model="CHANGED_MODEL")
        self.service.detect_and_persist_conflicts(draft_id, tmp_csv)
        items = self.db.get_draft_items(draft_id)
        conflict_item = [it for it in items if it["conflict_status"] == CONFLICT_STATUS_PENDING][0]
        self.service.keep_original_draft_item(conflict_item["id"], "保持原样", operator="张三")
        log = self.service.get_draft_operation_log(draft_id)
        resolve_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_CONFLICT_RESOLVE]
        self.assertEqual(len(resolve_ops), 1)
        self.assertEqual(resolve_ops[0]["operator"], "张三")
        self.assertIn("C001", resolve_ops[0]["detail"])

    def test_withdraw_logs_operation(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接")
        self.service.withdraw_takeover(draft_id, "张三", "撤回")
        log = self.service.get_draft_operation_log(draft_id)
        withdraw_ops = [l for l in log if l["operation"] == DRAFT_OPERATION_WITHDRAW_TAKEOVER]
        self.assertEqual(len(withdraw_ops), 1)


class TestDraftCSVImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_csv_import.db")

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

    def test_create_draft_from_csv(self):
        tmp_csv = os.path.join(self.tmpdir, "import_test.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", "张三"])
            w.writerow(["T002", "PAR64", "李四"])
        result = self.service.create_draft_from_csv(tmp_csv, "张三", remark="CSV导入测试")
        draft_id = result["draft_id"]
        draft = self.db.get_draft_batch(draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["operator"], "张三")
        self.assertEqual(draft["remark"], "CSV导入测试")
        items = self.db.get_draft_items(draft_id)
        self.assertEqual(len(items), 2)

    def test_create_draft_from_csv_missing_operator_fails(self):
        tmp_csv = os.path.join(self.tmpdir, "import_no_op.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", "张三"])
        with self.assertRaises(ValueError) as ctx:
            self.service.create_draft_from_csv(tmp_csv, "")
        self.assertIn("操作人", str(ctx.exception))

    def test_create_draft_from_csv_nonexistent_file_fails(self):
        with self.assertRaises(ValueError) as ctx:
            self.service.create_draft_from_csv("/nonexistent/file.csv", "张三")
        self.assertIn("CSV文件不存在", str(ctx.exception))

    def test_create_draft_from_csv_empty_file_fails(self):
        tmp_csv = os.path.join(self.tmpdir, "empty.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
        with self.assertRaises(ValueError) as ctx:
            self.service.create_draft_from_csv(tmp_csv, "张三")
        self.assertIn("没有可导入", str(ctx.exception))


class TestDraftCSVExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_csv_export.db")

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
            {"row": 2, "fixture_no": "T001", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "LED200", "person_in_charge": operator, "status": "可用"},
             "record": {"fixture_no": "T001", "model": "LED200", "person_in_charge": operator}},
        ]
        summary = {"total": 1, RESULT_NEW: 1, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_import.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", operator])
        result = self.service.create_draft_from_precheck(tmp_csv, operator, precheck_results, summary)
        return result["draft_id"]

    def test_export_marks_exported_before_submit(self):
        draft_id = self._create_test_draft("张三")
        export_dir = os.path.join(self.tmpdir, "export_marks_test")
        os.makedirs(export_dir, exist_ok=True)
        self.service.export_draft_items(draft_id, export_dir, "test.csv", operator="张三")
        draft = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft["exported_before_submit"], 1)
        self.assertTrue(draft["last_export_at"])

    def test_export_before_submit_reflected_in_submit(self):
        draft_id = self._create_test_draft("张三")
        export_dir = os.path.join(self.tmpdir, "export_before_submit_test")
        os.makedirs(export_dir, exist_ok=True)
        self.service.export_draft_items(draft_id, export_dir, "test.csv", operator="张三")
        result = self.service.submit_draft(draft_id, "张三")
        batch_id = result["batch_id"]
        items = self.db.get_import_batch_items(batch_id)
        self.assertTrue(any("导出后提交" in it.get("error_message", "") for it in items))

    def test_export_selected_only(self):
        draft_id = self._create_test_draft("张三")
        export_dir = os.path.join(self.tmpdir, "export_selected_test")
        os.makedirs(export_dir, exist_ok=True)
        path = self.service.export_draft_items(draft_id, export_dir, "selected.csv", selected_only=True)
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            rows = list(reader)
        self.assertEqual(len(rows), 1)

    def test_export_nonexistent_dir_fails(self):
        draft_id = self._create_test_draft("张三")
        with self.assertRaises(FileNotFoundError):
            self.service.export_draft_items(draft_id, "/nonexistent/dir", "test.csv")


class TestDraftFilterQuery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_filter.db")

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

    def _create_draft_with_source(self, operator, source_name):
        precheck_results = [
            {"row": 2, "fixture_no": "T001", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "LED200", "person_in_charge": operator, "status": "可用"},
             "record": {"fixture_no": "T001", "model": "LED200", "person_in_charge": operator}},
        ]
        summary = {"total": 1, RESULT_NEW: 1, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, source_name)
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", operator])
        return self.service.create_draft_from_precheck(tmp_csv, operator, precheck_results, summary)

    def test_filter_by_creator(self):
        self._create_draft_with_source("张三", "src_a.csv")
        self._create_draft_with_source("李四", "src_b.csv")
        drafts = self.service.query_drafts(creator="张三")
        self.assertTrue(all(d["operator"] == "张三" for d in drafts))
        self.assertTrue(len(drafts) >= 1)

    def test_filter_by_current_operator(self):
        result = self._create_draft_with_source("张三", "src_c.csv")
        self.service.takeover_draft(result["draft_id"], "李四", "交接")
        self._create_draft_with_source("王五", "src_d.csv")
        drafts = self.service.query_drafts(current_operator="李四")
        self.assertTrue(all(d.get("current_operator") == "李四" for d in drafts))

    def test_filter_by_status(self):
        result = self._create_draft_with_source("张三", "src_e.csv")
        self.service.submit_draft(result["draft_id"], "张三")
        self._create_draft_with_source("李四", "src_f.csv")
        drafts = self.service.query_drafts(status=DRAFT_STATUS_SUBMITTED)
        self.assertTrue(all(d["status"] == DRAFT_STATUS_SUBMITTED for d in drafts))

    def test_filter_by_date(self):
        drafts_all = self.service.query_drafts()
        self.assertIsInstance(drafts_all, list)

    def test_filter_by_conflict_status(self):
        self.db.add_fixture("C001", "PAR64", "", "A-01", "2027-12-31", "张三")
        precheck_results = [
            {"row": 2, "fixture_no": "C001", "result": RESULT_UPDATE, "errors": [], "warnings": [],
             "before": {"id": 1, "model": "PAR64", "person_in_charge": "张三", "status": "可用"},
             "after": {"model": "LED300", "person_in_charge": "张三", "status": "可用"},
             "record": {"fixture_no": "C001", "model": "LED300", "person_in_charge": "张三"}},
        ]
        summary = {"total": 1, RESULT_NEW: 0, RESULT_UPDATE: 1, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "conflict_src.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["C001", "LED300", "张三"])
        result = self.service.create_draft_from_precheck(tmp_csv, "张三", precheck_results, summary)
        draft_id = result["draft_id"]
        fixture = self.db.get_fixture_by_no("C001")
        self.db.update_fixture(fixture["id"], model="CHANGED")
        self.service.detect_and_persist_conflicts(draft_id, tmp_csv)
        drafts_with_pending = self.service.query_drafts(conflict_status=CONFLICT_STATUS_PENDING)
        self.assertTrue(len(drafts_with_pending) >= 1)

    def test_get_draft_batches_returns_all_active(self):
        self._create_draft_with_source("张三", "batch_a.csv")
        result = self._create_draft_with_source("李四", "batch_b.csv")
        self.service.submit_draft(result["draft_id"], "李四")
        drafts = self.db.get_draft_batches()
        statuses = set(d["status"] for d in drafts)
        self.assertNotIn(DRAFT_STATUS_DELETED, statuses)

    def test_get_all_draft_operators(self):
        self._create_draft_with_source("张三", "ops_a.csv")
        self._create_draft_with_source("李四", "ops_b.csv")
        operators = self.service.get_draft_operators()
        self.assertIn("张三", operators)
        self.assertIn("李四", operators)


class TestDraftRestartPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_restart.db")

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
            {"row": 2, "fixture_no": "T001", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "LED200", "person_in_charge": operator, "status": "可用"},
             "record": {"fixture_no": "T001", "model": "LED200", "person_in_charge": operator}},
        ]
        summary = {"total": 1, RESULT_NEW: 1, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_import.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", operator])
        result = self.service.create_draft_from_precheck(tmp_csv, operator, precheck_results, summary)
        return result["draft_id"]

    def test_full_handover_persistence_after_restart(self):
        draft_id = self._create_test_draft("张三")
        self.service.takeover_draft(draft_id, "李四", "交接A")
        self.service.assign_draft(draft_id, "李四", "王五", "分派给王五")
        handover_log = self.service.get_draft_handover_log(draft_id)
        op_log = self.service.get_draft_operation_log(draft_id)
        draft_before = self.db.get_draft_batch(draft_id)

        self.db.close()
        db2 = DatabaseManager(self.db_path)
        svc2 = LightingService(db2)

        draft_after = db2.get_draft_batch(draft_id)
        self.assertEqual(draft_after["current_operator"], "王五")
        self.assertEqual(draft_after["assigned_to"], "王五")
        self.assertEqual(draft_after["assigned_by"], "李四")

        handover_log2 = svc2.get_draft_handover_log(draft_id)
        self.assertEqual(len(handover_log2), 2)

        op_log2 = svc2.get_draft_operation_log(draft_id)
        self.assertEqual(len(op_log2), len(op_log))

        db2.close()
        self.db = DatabaseManager(self.db_path)
        self.service = LightingService(self.db)

    def test_partial_submit_persistence_after_restart(self):
        precheck_results = [
            {"row": 2, "fixture_no": "T001", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "LED200", "person_in_charge": "张三", "status": "可用"},
             "record": {"fixture_no": "T001", "model": "LED200", "person_in_charge": "张三"}},
            {"row": 3, "fixture_no": "T002", "result": RESULT_NEW, "errors": [], "warnings": [],
             "before": None, "after": {"model": "PAR64", "person_in_charge": "张三", "status": "可用"},
             "record": {"fixture_no": "T002", "model": "PAR64", "person_in_charge": "张三"}},
        ]
        summary = {"total": 2, RESULT_NEW: 2, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_partial_restart.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["T001", "LED200", "张三"])
            w.writerow(["T002", "PAR64", "张三"])
        result = self.service.create_draft_from_precheck(tmp_csv, "张三", precheck_results, summary)
        draft_id = result["draft_id"]
        items = self.db.get_draft_items(draft_id)
        self.service.set_draft_item_selected(items[0]["id"], False)
        self.service.submit_draft(draft_id, "张三")

        draft_before = self.db.get_draft_batch(draft_id)
        self.assertEqual(draft_before["status"], DRAFT_STATUS_PARTIAL)
        self.assertEqual(draft_before["submit_count"], 1)

        self.db.close()
        db2 = DatabaseManager(self.db_path)
        draft_after = db2.get_draft_batch(draft_id)
        self.assertEqual(draft_after["status"], DRAFT_STATUS_PARTIAL)
        self.assertEqual(draft_after["submit_count"], 1)
        items2 = db2.get_draft_items(draft_id)
        submitted_count = sum(1 for it in items2 if it.get("submitted"))
        self.assertEqual(submitted_count, 1)
        db2.close()
        self.db = DatabaseManager(self.db_path)
        self.service = LightingService(self.db)

    def test_conflict_resolution_persistence_after_restart(self):
        self.db.add_fixture("C001", "PAR64", "", "A-01", "2027-12-31", "张三")
        precheck_results = [
            {"row": 2, "fixture_no": "C001", "result": RESULT_UPDATE, "errors": [], "warnings": [],
             "before": {"id": 1, "model": "PAR64", "person_in_charge": "张三", "status": "可用"},
             "after": {"model": "LED300", "person_in_charge": "张三", "status": "可用"},
             "record": {"fixture_no": "C001", "model": "LED300", "person_in_charge": "张三"}},
        ]
        summary = {"total": 1, RESULT_NEW: 0, RESULT_UPDATE: 1, RESULT_SKIP: 0, RESULT_ERROR: 0}
        tmp_csv = os.path.join(self.tmpdir, "test_conflict_restart.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["灯具编号", "型号", "负责人"])
            w.writerow(["C001", "LED300", "张三"])
        result = self.service.create_draft_from_precheck(tmp_csv, "张三", precheck_results, summary)
        draft_id = result["draft_id"]
        fixture = self.db.get_fixture_by_no("C001")
        self.db.update_fixture(fixture["id"], model="CHANGED")
        self.service.detect_and_persist_conflicts(draft_id, tmp_csv)
        items = self.db.get_draft_items(draft_id)
        conflict_item = [it for it in items if it["conflict_status"] == CONFLICT_STATUS_PENDING][0]
        self.service.keep_original_draft_item(conflict_item["id"], "保持原样", operator="张三")

        self.db.close()
        db2 = DatabaseManager(self.db_path)
        items2 = db2.get_draft_items(draft_id)
        same_item = [it for it in items2 if it["id"] == conflict_item["id"]][0]
        self.assertEqual(same_item["resolution_action"], "original")
        self.assertEqual(same_item["resolution_note"], "保持原样")
        self.assertEqual(same_item["conflict_status"], CONFLICT_STATUS_RESOLVED)
        db2.close()
        self.db = DatabaseManager(self.db_path)
        self.service = LightingService(self.db)

    def test_export_flag_persistence_after_restart(self):
        draft_id = self._create_test_draft("张三")
        export_dir = os.path.join(self.tmpdir, "export_persist_test")
        os.makedirs(export_dir, exist_ok=True)
        self.service.export_draft_items(draft_id, export_dir, "test.csv", operator="张三")

        self.db.close()
        db2 = DatabaseManager(self.db_path)
        draft = db2.get_draft_batch(draft_id)
        self.assertEqual(draft["exported_before_submit"], 1)
        self.assertTrue(draft["last_export_at"])
        self.assertEqual(draft["last_export_by"], "张三")
        db2.close()
        self.db = DatabaseManager(self.db_path)
        self.service = LightingService(self.db)


class TestDraftDBSchema(unittest.TestCase):
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

    def test_operation_log_table_exists(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "schema_test.db")
        try:
            db = DatabaseManager(db_path)
            row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='draft_operation_log'").fetchone()
            self.assertIsNotNone(row)
            db.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_draft_batch_has_all_handover_columns(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "col_test.db")
        try:
            db = DatabaseManager(db_path)
            cols = [r[1] for r in db.execute("PRAGMA table_info(import_draft_batches)").fetchall()]
            for col in ["current_operator", "handover_reason", "handover_at",
                        "assigned_by", "assigned_at", "assigned_to", "assign_reason",
                        "last_operation", "last_operation_at", "last_operation_by",
                        "last_export_at", "last_export_by", "exported_before_submit",
                        "submit_count"]:
                self.assertIn(col, cols, f"Missing column: {col}")
            db.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_draft_items_has_resolved_by(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "col_test.db")
        try:
            db = DatabaseManager(db_path)
            cols = [r[1] for r in db.execute("PRAGMA table_info(import_draft_items)").fetchall()]
            self.assertIn("resolved_by", cols)
            self.assertIn("submitted", cols)
            self.assertIn("submitted_at", cols)
            db.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_handover_log_has_handover_type(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "col_test.db")
        try:
            db = DatabaseManager(db_path)
            cols = [r[1] for r in db.execute("PRAGMA table_info(draft_handover_log)").fetchall()]
            self.assertIn("handover_type", cols)
            self.assertIn("operator", cols)
            db.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
