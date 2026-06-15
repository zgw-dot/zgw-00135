#!/usr/bin/env python3
"""Stage Lighting Rental & Inspection Management Tool"""

import sqlite3
import json
import csv
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import date, datetime
from pathlib import Path

APP_TITLE = "舞台灯光租借巡检管理系统"
DB_FILE = Path(__file__).parent / "stage_lighting.db"

STATUS_AVAILABLE = "可用"
STATUS_BORROWED = "借出"
STATUS_RETURN_PENDING = "待归还复核"
STATUS_INSPECTION_FREEZE = "巡检冻结"
STATUS_MAINTENANCE_FREEZE = "维修冻结"
STATUS_SCRAPPED = "报废"

ALL_STATUSES = [
    STATUS_AVAILABLE,
    STATUS_BORROWED,
    STATUS_RETURN_PENDING,
    STATUS_INSPECTION_FREEZE,
    STATUS_MAINTENANCE_FREEZE,
    STATUS_SCRAPPED,
]

STATUS_COLORS = {
    STATUS_AVAILABLE: "#2e7d32",
    STATUS_BORROWED: "#e65100",
    STATUS_RETURN_PENDING: "#f9a825",
    STATUS_INSPECTION_FREEZE: "#1565c0",
    STATUS_MAINTENANCE_FREEZE: "#c62828",
    STATUS_SCRAPPED: "#616161",
}

TRANSITIONS = {
    STATUS_AVAILABLE: [STATUS_BORROWED, STATUS_INSPECTION_FREEZE, STATUS_MAINTENANCE_FREEZE, STATUS_SCRAPPED],
    STATUS_BORROWED: [STATUS_RETURN_PENDING, STATUS_INSPECTION_FREEZE, STATUS_MAINTENANCE_FREEZE, STATUS_SCRAPPED],
    STATUS_RETURN_PENDING: [STATUS_AVAILABLE, STATUS_INSPECTION_FREEZE, STATUS_MAINTENANCE_FREEZE, STATUS_SCRAPPED],
    STATUS_INSPECTION_FREEZE: [STATUS_AVAILABLE, STATUS_SCRAPPED],
    STATUS_MAINTENANCE_FREEZE: [STATUS_AVAILABLE, STATUS_SCRAPPED],
    STATUS_SCRAPPED: [],
}

TABLE_COLUMNS = [
    ("id", "ID", 40),
    ("fixture_no", "编号", 90),
    ("model", "型号", 100),
    ("accessories", "配件", 120),
    ("location", "库位", 80),
    ("inspection_due_date", "巡检到期", 100),
    ("person_in_charge", "负责人", 80),
    ("status", "状态", 100),
    ("last_remark", "最近备注", 160),
]

EXPORT_FIELDS = ["fixture_no", "model", "accessories", "location", "inspection_due_date", "person_in_charge", "status", "last_remark"]
EXPORT_HEADERS = ["灯具编号", "型号", "配件", "库位", "巡检到期日", "负责人", "状态", "最近备注"]

SETTING_FILTER_LOCATION = "filter_location"
SETTING_FILTER_STATUS = "filter_status"
SETTING_FILTER_DUE_START = "filter_due_start"
SETTING_FILTER_DUE_END = "filter_due_end"

IMPORT_REQUIRED_FIELDS = ["fixture_no", "model", "person_in_charge"]
IMPORT_FIELD_MAPPING_CSV = {
    "灯具编号": "fixture_no",
    "型号": "model",
    "配件": "accessories",
    "库位": "location",
    "巡检到期日": "inspection_due_date",
    "负责人": "person_in_charge",
    "状态": "status",
    "最近备注": "last_remark",
}
IMPORT_FIELD_MAPPING_JSON = {v: v for v in IMPORT_FIELD_MAPPING_CSV.values()}

RESULT_NEW = "新增"
RESULT_UPDATE = "更新"
RESULT_SKIP = "跳过"
RESULT_ERROR = "错误"

ROLLBACK_SAFE_STATUSES = {STATUS_AVAILABLE}


class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS fixtures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fixture_no TEXT UNIQUE NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                accessories TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                inspection_due_date TEXT NOT NULL DEFAULT '',
                person_in_charge TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '可用',
                last_remark TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fixture_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                from_status TEXT NOT NULL DEFAULT '',
                to_status TEXT NOT NULL DEFAULT '',
                operator TEXT NOT NULL DEFAULT '',
                remark TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (fixture_id) REFERENCES fixtures(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no TEXT UNIQUE NOT NULL,
                operator TEXT NOT NULL DEFAULT '',
                source_file TEXT NOT NULL DEFAULT '',
                record_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                update_count INTEGER NOT NULL DEFAULT 0,
                skip_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS import_batch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                fixture_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                result TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                before_snapshot TEXT NOT NULL DEFAULT '',
                after_snapshot TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (batch_id) REFERENCES import_batches(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS import_draft_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_no TEXT UNIQUE NOT NULL,
                operator TEXT NOT NULL DEFAULT '',
                source_file TEXT NOT NULL DEFAULT '',
                source_file_hash TEXT NOT NULL DEFAULT '',
                filter_conditions TEXT NOT NULL DEFAULT '',
                remark TEXT NOT NULL DEFAULT '',
                record_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                update_count INTEGER NOT NULL DEFAULT 0,
                skip_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS import_draft_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                fixture_no TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                result TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                before_snapshot TEXT NOT NULL DEFAULT '',
                after_snapshot TEXT NOT NULL DEFAULT '',
                record_data TEXT NOT NULL DEFAULT '',
                selected INTEGER NOT NULL DEFAULT 1,
                conflict_type TEXT NOT NULL DEFAULT '',
                conflict_detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (draft_id) REFERENCES import_draft_batches(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_fixtures_status ON fixtures(status);
            CREATE INDEX IF NOT EXISTS idx_fixtures_location ON fixtures(location);
            CREATE INDEX IF NOT EXISTS idx_fixtures_inspection ON fixtures(inspection_due_date);
            CREATE INDEX IF NOT EXISTS idx_history_fid ON history(fixture_id);
            CREATE INDEX IF NOT EXISTS idx_import_batches_no ON import_batches(batch_no);
            CREATE INDEX IF NOT EXISTS idx_import_items_batch ON import_batch_items(batch_id);
            CREATE INDEX IF NOT EXISTS idx_import_items_fno ON import_batch_items(fixture_no);
            CREATE INDEX IF NOT EXISTS idx_import_draft_no ON import_draft_batches(draft_no);
            CREATE INDEX IF NOT EXISTS idx_import_draft_items_draft ON import_draft_items(draft_id);
            CREATE INDEX IF NOT EXISTS idx_import_draft_items_fno ON import_draft_items(fixture_no);
        """)
        self.conn.commit()

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    def add_fixture(self, fixture_no, model, accessories, location, inspection_due_date, person_in_charge):
        cur = self.execute(
            "INSERT INTO fixtures (fixture_no,model,accessories,location,inspection_due_date,person_in_charge) VALUES (?,?,?,?,?,?)",
            (fixture_no, model, accessories, location, inspection_due_date, person_in_charge),
        )
        self.commit()
        return cur.lastrowid

    def get_fixture(self, fixture_id):
        row = self.execute("SELECT * FROM fixtures WHERE id=?", (fixture_id,)).fetchone()
        return dict(row) if row else None

    def get_fixture_by_no(self, fixture_no):
        row = self.execute("SELECT * FROM fixtures WHERE fixture_no=?", (fixture_no,)).fetchone()
        return dict(row) if row else None

    def update_fixture_status(self, fixture_id, status, remark=""):
        self.execute(
            "UPDATE fixtures SET status=?, last_remark=?, updated_at=datetime('now','localtime') WHERE id=?",
            (status, remark, fixture_id),
        )
        self.commit()

    def update_fixture(self, fixture_id, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [fixture_id]
        self.execute(f"UPDATE fixtures SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", vals)
        self.commit()

    def query_fixtures(self, location=None, status=None, due_start=None, due_end=None):
        sql = "SELECT * FROM fixtures WHERE 1=1"
        params = []
        if location:
            sql += " AND location=?"
            params.append(location)
        if status:
            sql += " AND status=?"
            params.append(status)
        if due_start:
            sql += " AND inspection_due_date>=?"
            params.append(due_start)
        if due_end:
            sql += " AND inspection_due_date<=?"
            params.append(due_end)
        sql += " ORDER BY fixture_no"
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def get_all_locations(self):
        return [r["location"] for r in self.execute("SELECT DISTINCT location FROM fixtures WHERE location!='' ORDER BY location").fetchall()]

    def delete_fixture(self, fixture_id):
        self.execute("DELETE FROM history WHERE fixture_id=?", (fixture_id,))
        self.execute("DELETE FROM fixtures WHERE id=?", (fixture_id,))
        self.commit()

    def add_history(self, fixture_id, action, from_status, to_status, operator, remark=""):
        self.execute(
            "INSERT INTO history (fixture_id,action,from_status,to_status,operator,remark) VALUES (?,?,?,?,?,?)",
            (fixture_id, action, from_status, to_status, operator, remark),
        )
        self.commit()

    def get_history(self, fixture_id):
        return [dict(r) for r in self.execute("SELECT * FROM history WHERE fixture_id=? ORDER BY created_at DESC", (fixture_id,)).fetchall()]

    def get_setting(self, key, default=""):
        row = self.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        self.commit()

    def get_status_counts(self):
        rows = self.execute("SELECT status, COUNT(*) as cnt FROM fixtures GROUP BY status").fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def create_import_batch(self, batch_no, operator, source_file, record_count):
        cur = self.execute(
            "INSERT INTO import_batches (batch_no, operator, source_file, record_count, status) VALUES (?,?,?,?,'pending')",
            (batch_no, operator, source_file, record_count),
        )
        return cur.lastrowid

    def update_import_batch_status(self, batch_id, status, error_message=""):
        self.execute(
            "UPDATE import_batches SET status=?, error_message=? WHERE id=?",
            (status, error_message, batch_id),
        )

    def update_import_batch_counts(self, batch_id, new_count, update_count, skip_count, error_count):
        self.execute(
            "UPDATE import_batches SET new_count=?, update_count=?, skip_count=?, error_count=? WHERE id=?",
            (new_count, update_count, skip_count, error_count, batch_id),
        )

    def add_import_batch_item(self, batch_id, fixture_no, row_index, result, error_message="", before_snapshot="", after_snapshot=""):
        cur = self.execute(
            "INSERT INTO import_batch_items (batch_id, fixture_no, row_index, result, error_message, before_snapshot, after_snapshot) VALUES (?,?,?,?,?,?,?)",
            (batch_id, fixture_no, row_index, result, error_message, before_snapshot, after_snapshot),
        )
        return cur.lastrowid

    def get_import_batches(self):
        rows = self.execute("SELECT * FROM import_batches ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_import_batch(self, batch_id):
        row = self.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def get_import_batch_by_no(self, batch_no):
        row = self.execute("SELECT * FROM import_batches WHERE batch_no=?", (batch_no,)).fetchone()
        return dict(row) if row else None

    def get_import_batch_items(self, batch_id):
        rows = self.execute("SELECT * FROM import_batch_items WHERE batch_id=? ORDER BY row_index", (batch_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_import_batch_errors(self, batch_id):
        rows = self.execute("SELECT * FROM import_batch_items WHERE batch_id=? AND result=? ORDER BY row_index",
                              (batch_id, RESULT_ERROR)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_batch_for_fixture(self, fixture_no):
        row = self.execute(
            "SELECT bi.*, b.batch_no, b.status as batch_status FROM import_batch_items bi "
            "JOIN import_batches b ON bi.batch_id = b.id "
            "WHERE bi.fixture_no=? AND bi.result IN (?,?) AND b.status='completed' "
            "ORDER BY bi.created_at DESC LIMIT 1",
            (fixture_no, RESULT_NEW, RESULT_UPDATE),
        ).fetchone()
        return dict(row) if row else None

    def delete_import_batch(self, batch_id):
        self.execute("DELETE FROM import_batch_items WHERE batch_id=?", (batch_id,))
        self.execute("DELETE FROM import_batches WHERE id=?", (batch_id,))
        self.commit()

    def create_draft_batch(self, draft_no, operator, source_file, source_file_hash, filter_conditions, remark, record_count, new_count, update_count, skip_count, error_count):
        cur = self.execute(
            "INSERT INTO import_draft_batches (draft_no, operator, source_file, source_file_hash, filter_conditions, remark, record_count, new_count, update_count, skip_count, error_count, status) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'draft')",
            (draft_no, operator, source_file, source_file_hash, filter_conditions or "", remark or "", record_count, new_count, update_count, skip_count, error_count),
        )
        return cur.lastrowid

    def update_draft_batch(self, draft_id, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [draft_id]
        self.execute(f"UPDATE import_draft_batches SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", vals)
        self.commit()

    def get_draft_batches(self):
        rows = self.execute("SELECT * FROM import_draft_batches WHERE status='draft' ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_draft_batch(self, draft_id):
        row = self.execute("SELECT * FROM import_draft_batches WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def get_draft_batch_by_no(self, draft_no):
        row = self.execute("SELECT * FROM import_draft_batches WHERE draft_no=?", (draft_no,)).fetchone()
        return dict(row) if row else None

    def add_draft_item(self, draft_id, fixture_no, row_index, result, error_message="", before_snapshot="", after_snapshot="", record_data="", selected=1, conflict_type="", conflict_detail=""):
        cur = self.execute(
            "INSERT INTO import_draft_items (draft_id, fixture_no, row_index, result, error_message, before_snapshot, after_snapshot, record_data, selected, conflict_type, conflict_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (draft_id, fixture_no, row_index, result, error_message, before_snapshot, after_snapshot, record_data, 1 if selected else 0, conflict_type, conflict_detail),
        )
        return cur.lastrowid

    def update_draft_item(self, item_id, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [item_id]
        self.execute(f"UPDATE import_draft_items SET {sets} WHERE id=?", vals)
        self.commit()

    def get_draft_items(self, draft_id):
        rows = self.execute("SELECT * FROM import_draft_items WHERE draft_id=? ORDER BY row_index", (draft_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_selected_draft_items(self, draft_id):
        rows = self.execute("SELECT * FROM import_draft_items WHERE draft_id=? AND selected=1 ORDER BY row_index", (draft_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_draft_item_selected(self, item_id, selected):
        self.execute("UPDATE import_draft_items SET selected=? WHERE id=?", (1 if selected else 0, item_id))
        self.commit()

    def set_all_draft_items_selected(self, draft_id, selected):
        self.execute("UPDATE import_draft_items SET selected=? WHERE draft_id=?", (1 if selected else 0, draft_id))
        self.commit()

    def set_draft_items_selected_by_result(self, draft_id, result, selected):
        self.execute("UPDATE import_draft_items SET selected=? WHERE draft_id=? AND result=?", (1 if selected else 0, draft_id, result))
        self.commit()

    def delete_draft_batch(self, draft_id):
        self.execute("DELETE FROM import_draft_items WHERE draft_id=?", (draft_id,))
        self.execute("DELETE FROM import_draft_batches WHERE id=?", (draft_id,))
        self.commit()

    def count_draft_items_by_result(self, draft_id):
        rows = self.execute("SELECT result, COUNT(*) as cnt, SUM(selected) as sel_cnt FROM import_draft_items WHERE draft_id=? GROUP BY result", (draft_id,)).fetchall()
        return {r["result"]: {"total": r["cnt"], "selected": r["sel_cnt"]} for r in rows}

    def begin_transaction(self):
        self.execute("BEGIN IMMEDIATE")

    def rollback_transaction(self):
        self.conn.rollback()


class LightingService:
    def __init__(self, db):
        self.db = db

    def add_fixture(self, fixture_no, model, accessories, location, inspection_due_date, person_in_charge):
        if self.db.get_fixture_by_no(fixture_no):
            raise ValueError(f"灯具编号 '{fixture_no}' 已存在")
        fid = self.db.add_fixture(fixture_no, model, accessories, location, inspection_due_date, person_in_charge)
        self.db.add_history(fid, "添加灯具", "", STATUS_AVAILABLE, person_in_charge, "新建灯具")
        return fid

    def _validate_transition(self, current, target):
        allowed = TRANSITIONS.get(current, [])
        if target not in allowed:
            if current == target:
                raise ValueError(f"灯具已处于「{current}」状态，无需重复操作")
            raise ValueError(f"不允许从「{current}」转换到「{target}」")

    def borrow(self, fixture_id, operator, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self._validate_transition(f["status"], STATUS_BORROWED)
        if f["inspection_due_date"]:
            if f["inspection_due_date"] < date.today().isoformat():
                raise ValueError(f"巡检已过期（到期日: {f['inspection_due_date']}），无法借出。请先完成巡检。")
        old = f["status"]
        self.db.update_fixture_status(fixture_id, STATUS_BORROWED, remark)
        self.db.add_history(fixture_id, "借出", old, STATUS_BORROWED, operator, remark)

    def return_fixture(self, fixture_id, operator, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        if f["status"] == STATUS_RETURN_PENDING:
            raise ValueError("该灯具已提交归还，处于待复核状态，请勿重复提交")
        self._validate_transition(f["status"], STATUS_RETURN_PENDING)
        old = f["status"]
        self.db.update_fixture_status(fixture_id, STATUS_RETURN_PENDING, remark)
        self.db.add_history(fixture_id, "归还登记", old, STATUS_RETURN_PENDING, operator, remark)

    def review_return(self, fixture_id, operator, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self._validate_transition(f["status"], STATUS_AVAILABLE)
        old = f["status"]
        self.db.update_fixture_status(fixture_id, STATUS_AVAILABLE, remark)
        self.db.add_history(fixture_id, "复核入库", old, STATUS_AVAILABLE, operator, remark)

    def freeze_inspection(self, fixture_id, operator, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self._validate_transition(f["status"], STATUS_INSPECTION_FREEZE)
        old = f["status"]
        self.db.update_fixture_status(fixture_id, STATUS_INSPECTION_FREEZE, remark)
        self.db.add_history(fixture_id, "巡检冻结", old, STATUS_INSPECTION_FREEZE, operator, remark)

    def unfreeze_inspection(self, fixture_id, operator, new_due_date, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self._validate_transition(f["status"], STATUS_AVAILABLE)
        if operator != f["person_in_charge"]:
            raise ValueError(f"只有负责人「{f['person_in_charge']}」才能解除巡检冻结，当前操作人「{operator}」无权限")
        old = f["status"]
        self.db.update_fixture(fixture_id, status=STATUS_AVAILABLE, last_remark=remark, inspection_due_date=new_due_date)
        self.db.add_history(fixture_id, "巡检解冻", old, STATUS_AVAILABLE, operator, f"新巡检到期日: {new_due_date}; {remark}")

    def freeze_maintenance(self, fixture_id, operator, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self._validate_transition(f["status"], STATUS_MAINTENANCE_FREEZE)
        old = f["status"]
        self.db.update_fixture_status(fixture_id, STATUS_MAINTENANCE_FREEZE, remark)
        self.db.add_history(fixture_id, "维修冻结", old, STATUS_MAINTENANCE_FREEZE, operator, remark)

    def unfreeze_maintenance(self, fixture_id, operator, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self._validate_transition(f["status"], STATUS_AVAILABLE)
        if operator != f["person_in_charge"]:
            raise ValueError(f"只有负责人「{f['person_in_charge']}」才能解除维修冻结，当前操作人「{operator}」无权限")
        old = f["status"]
        self.db.update_fixture_status(fixture_id, STATUS_AVAILABLE, remark)
        self.db.add_history(fixture_id, "维修解冻", old, STATUS_AVAILABLE, operator, remark)

    def scrap(self, fixture_id, operator, remark=""):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self._validate_transition(f["status"], STATUS_SCRAPPED)
        old = f["status"]
        self.db.update_fixture_status(fixture_id, STATUS_SCRAPPED, remark)
        self.db.add_history(fixture_id, "报废", old, STATUS_SCRAPPED, operator, remark)

    def update_fixture_info(self, fixture_id, model, accessories, location, inspection_due_date, person_in_charge):
        f = self.db.get_fixture(fixture_id)
        if not f:
            raise ValueError("灯具不存在")
        self.db.update_fixture(fixture_id, model=model, accessories=accessories, location=location,
                               inspection_due_date=inspection_due_date, person_in_charge=person_in_charge)
        self.db.add_history(fixture_id, "编辑信息", f["status"], f["status"], f["person_in_charge"], "修改灯具基本信息")

    def get_fixtures(self, **filters):
        return self.db.query_fixtures(**filters)

    def get_history(self, fixture_id):
        return self.db.get_history(fixture_id)

    def get_all_locations(self):
        return self.db.get_all_locations()

    def get_export_dir(self):
        return self.db.get_setting("export_dir", str(Path.home()))

    def set_export_dir(self, path):
        self.db.set_setting("export_dir", path)

    def export_csv(self, fixtures, directory, filename):
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"目录 '{directory}' 不存在")
        if not os.access(directory, os.W_OK):
            raise PermissionError(f"导出目录 '{directory}' 不可写，请选择其他目录")
        filepath = dir_path / filename
        with open(filepath, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(EXPORT_HEADERS)
            for fix in fixtures:
                writer.writerow([fix.get(k, "") for k in EXPORT_FIELDS])
        return str(filepath)

    def export_json(self, fixtures, directory, filename):
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"目录 '{directory}' 不存在")
        if not os.access(directory, os.W_OK):
            raise PermissionError(f"导出目录 '{directory}' 不可写，请选择其他目录")
        filepath = dir_path / filename
        data = [{k: fix.get(k, "") for k in EXPORT_FIELDS} for fix in fixtures]
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return str(filepath)

    def get_status_counts(self):
        return self.db.get_status_counts()

    def _parse_csv(self, filepath):
        records = []
        with open(filepath, "r", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames or []
            for col in fieldnames:
                if col not in IMPORT_FIELD_MAPPING_CSV:
                    raise ValueError(f"未知列名: '{col}'。允许的列名: {list(IMPORT_FIELD_MAPPING_CSV.keys())}")
            required_headers = [k for k, v in IMPORT_FIELD_MAPPING_CSV.items() if v in IMPORT_REQUIRED_FIELDS]
            missing = [h for h in required_headers if h not in fieldnames]
            if missing:
                raise ValueError(f"缺少必填列: {missing}")
            for i, row in enumerate(reader, start=2):
                mapped = {}
                for k, v in IMPORT_FIELD_MAPPING_CSV.items():
                    mapped[v] = (row.get(k) or "").strip()
                mapped["_row"] = i
                records.append(mapped)
        return records

    def _parse_json(self, filepath):
        records = []
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError("JSON 根节点必须是数组")
        required = ["fixture_no", "model", "person_in_charge"]
        for i, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"第 {i} 条记录不是对象")
            for k in required:
                if k not in item:
                    raise ValueError(f"第 {i} 条记录缺少必填字段: {k}")
            mapped = {}
            for k in IMPORT_FIELD_MAPPING_JSON:
                mapped[k] = (str(item.get(k, "")) or "").strip()
            mapped["_row"] = i
            records.append(mapped)
        return records

    def parse_import_file(self, filepath):
        ext = Path(filepath).suffix.lower()
        if ext == ".csv":
            return self._parse_csv(filepath)
        elif ext == ".json":
            return self._parse_json(filepath)
        else:
            raise ValueError(f"不支持的文件格式: {ext}。仅支持 .csv 和 .json")

    def _validate_date(self, date_str):
        if not date_str:
            return True, ""
        try:
            date.fromisoformat(date_str)
            return True, ""
        except ValueError:
            return False, f"日期格式错误，应为 YYYY-MM-DD"

    def _validate_record(self, record, existing_by_no, seen_in_file):
        errors = []
        warnings = []
        fixture_no = record.get("fixture_no", "")
        row = record.get("_row", "?")

        if not fixture_no:
            errors.append("灯具编号不能为空")
            return RESULT_ERROR, errors, warnings, None, None

        for field in IMPORT_REQUIRED_FIELDS:
            if not record.get(field, ""):
                label = {v: k for k, v in IMPORT_FIELD_MAPPING_CSV.items()}.get(field, field)
                errors.append(f"缺少必填字段: {label}")

        status_val = record.get("status", "")
        if status_val and status_val not in ALL_STATUSES:
            errors.append(f"状态值无效: '{status_val}'，允许值: {ALL_STATUSES}")

        date_ok, date_err = self._validate_date(record.get("inspection_due_date", ""))
        if not date_ok:
            errors.append(date_err)

        if fixture_no in seen_in_file:
            errors.append(f"文件内编号重复，第 {seen_in_file[fixture_no]} 行已出现")
        seen_in_file[fixture_no] = row

        existing = existing_by_no.get(fixture_no)
        if existing:
            if existing["status"] == STATUS_SCRAPPED:
                errors.append(f"灯具已报废，不允许覆盖")

        if errors:
            return RESULT_ERROR, errors, warnings, None, None

        if existing:
            changed = False
            for field in ["model", "accessories", "location", "inspection_due_date", "person_in_charge", "status"]:
                new_val = record.get(field, "")
                old_val = existing.get(field, "")
                if field == "status" and not new_val:
                    continue
                if new_val and new_val != old_val:
                    changed = True
                    break
            if not changed:
                return RESULT_SKIP, [], ["数据无变化"], existing, None
            return RESULT_UPDATE, [], [], existing, record
        else:
            return RESULT_NEW, [], [], None, record

    def precheck_import(self, records):
        results = []
        fixture_nos = [r["fixture_no"] for r in records if r.get("fixture_no")]
        placeholders = ",".join("?" * len(fixture_nos))
        existing_rows = []
        if fixture_nos:
            existing_rows = self.db.execute(
                f"SELECT * FROM fixtures WHERE fixture_no IN ({placeholders})",
                tuple(fixture_nos),
            ).fetchall()
        existing_by_no = {r["fixture_no"]: dict(r) for r in existing_rows}
        seen_in_file = {}

        for record in records:
            result, errors, warnings, before, after = self._validate_record(record, existing_by_no, seen_in_file)
            results.append({
                "row": record.get("_row", "?"),
                "fixture_no": record.get("fixture_no", ""),
                "result": result,
                "errors": errors,
                "warnings": warnings,
                "before": before,
                "after": after,
                "record": record,
            })

        summary = {
            RESULT_NEW: sum(1 for r in results if r["result"] == RESULT_NEW),
            RESULT_UPDATE: sum(1 for r in results if r["result"] == RESULT_UPDATE),
            RESULT_SKIP: sum(1 for r in results if r["result"] == RESULT_SKIP),
            RESULT_ERROR: sum(1 for r in results if r["result"] == RESULT_ERROR),
            "total": len(results),
        }
        return results, summary

    def execute_import(self, filepath, operator, source_name=None):
        if not operator:
            raise ValueError("操作人不能为空")

        records = self.parse_import_file(filepath)
        if not records:
            raise ValueError("文件中没有可导入的记录")

        source_name = source_name or Path(filepath).name
        batch_no = f"IMP{datetime.now().strftime('%Y%m%d%H%M%S')}{datetime.now().microsecond // 1000:03d}"
        precheck_results, summary = self.precheck_import(records)

        if summary[RESULT_ERROR] > 0:
            self.db.begin_transaction()
            try:
                batch_id = self.db.create_import_batch(batch_no, operator, source_name, len(records))
                counts = {RESULT_NEW: 0, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}
                for pr in precheck_results:
                    row_idx = pr["row"]
                    fixture_no = pr["fixture_no"]
                    result = pr["result"]
                    error_msg = "; ".join(pr["errors"]) if pr["errors"] else ""
                    before_snap = json.dumps(pr["before"], ensure_ascii=False) if pr["before"] else ""
                    after_snap = json.dumps(pr["after"], ensure_ascii=False) if pr["after"] else ""
                    self.db.add_import_batch_item(
                        batch_id, fixture_no, row_idx, result, error_msg, before_snap, after_snap
                    )
                    if result in counts:
                        counts[result] += 1
                self.db.update_import_batch_counts(
                    batch_id, counts[RESULT_NEW], counts[RESULT_UPDATE], counts[RESULT_SKIP], counts[RESULT_ERROR]
                )
                self.db.update_import_batch_status(batch_id, "failed")
                self.db.commit()
            except Exception:
                self.db.rollback_transaction()
                raise
            error_details = []
            for r in precheck_results:
                if r["result"] == RESULT_ERROR:
                    for e in r["errors"]:
                        error_details.append(f"第{r['row']}行 ({r['fixture_no']}): {e}")
            err = ValueError("预检发现错误，导入已取消:\n" + "\n".join(error_details) + f"\n(批次 {batch_no} 已记录，可在导入批次历史中查看并导出错误清单)")
            err.batch_id = batch_id
            err.batch_no = batch_no
            raise err

        self.db.begin_transaction()
        try:
            batch_id = self.db.create_import_batch(batch_no, operator, source_name, len(records))

            counts = {RESULT_NEW: 0, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}

            for pr in precheck_results:
                row_idx = pr["row"]
                fixture_no = pr["fixture_no"]
                result = pr["result"]
                record = pr["record"]
                before = pr["before"]
                after = pr["after"]

                before_snap = json.dumps(before, ensure_ascii=False) if before else ""
                after_snap = ""
                error_msg = ""

                if result == RESULT_NEW:
                    status_val = after.get("status") or STATUS_AVAILABLE
                    fid = self.db.execute(
                        "INSERT INTO fixtures (fixture_no,model,accessories,location,inspection_due_date,person_in_charge,status,last_remark) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            fixture_no,
                            after.get("model", ""),
                            after.get("accessories", ""),
                            after.get("location", ""),
                            after.get("inspection_due_date", ""),
                            after.get("person_in_charge", ""),
                            status_val,
                            after.get("last_remark", f"批量导入，批次 {batch_no}"),
                        ),
                    ).lastrowid
                    self.db.execute(
                        "INSERT INTO history (fixture_id,action,from_status,to_status,operator,remark) VALUES (?,?,?,?,?,?)",
                        (fid, "批量导入", "", status_val, operator, f"批次 {batch_no}"),
                    )
                    new_f = self.db.execute("SELECT * FROM fixtures WHERE id=?", (fid,)).fetchone()
                    after_snap = json.dumps(dict(new_f), ensure_ascii=False)
                    counts[RESULT_NEW] += 1

                elif result == RESULT_UPDATE:
                    fid = before["id"]
                    old_status = before["status"]
                    updates = {}
                    for field in ["model", "accessories", "location", "inspection_due_date", "person_in_charge", "status", "last_remark"]:
                        new_val = after.get(field, "")
                        old_val = before.get(field, "")
                        if field == "status" and not new_val:
                            continue
                        if new_val and new_val != old_val:
                            updates[field] = new_val

                    if updates:
                        sets = ", ".join(f"{k}=?" for k in updates)
                        vals = list(updates.values()) + [fid]
                        self.db.execute(f"UPDATE fixtures SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", vals)
                        new_status = updates.get("status", old_status)
                        remark = f"批量更新，批次 {batch_no}"
                        self.db.execute(
                            "INSERT INTO history (fixture_id,action,from_status,to_status,operator,remark) VALUES (?,?,?,?,?,?)",
                            (fid, "批量更新", old_status, new_status, operator, remark),
                        )
                    new_f = self.db.execute("SELECT * FROM fixtures WHERE id=?", (fid,)).fetchone()
                    after_snap = json.dumps(dict(new_f), ensure_ascii=False)
                    counts[RESULT_UPDATE] += 1

                elif result == RESULT_SKIP:
                    after_snap = before_snap
                    counts[RESULT_SKIP] += 1

                if pr["warnings"]:
                    error_msg = "; ".join(pr["warnings"])

                self.db.add_import_batch_item(
                    batch_id, fixture_no, row_idx, result, error_msg, before_snap, after_snap
                )

            self.db.update_import_batch_counts(
                batch_id, counts[RESULT_NEW], counts[RESULT_UPDATE], counts[RESULT_SKIP], counts[RESULT_ERROR]
            )
            self.db.update_import_batch_status(batch_id, "completed")
            self.db.commit()

            return {
                "batch_id": batch_id,
                "batch_no": batch_no,
                "summary": {
                    "total": summary["total"],
                    "new": counts[RESULT_NEW],
                    "update": counts[RESULT_UPDATE],
                    "skip": counts[RESULT_SKIP],
                    "error": counts[RESULT_ERROR],
                },
            }

        except Exception as e:
            self.db.rollback_transaction()
            raise

    def _get_status_after_import(self, fixture_id, import_created_at):
        row = self.db.execute(
            "SELECT to_status FROM history WHERE fixture_id=? AND created_at>? ORDER BY created_at ASC LIMIT 1",
            (fixture_id, import_created_at),
        ).fetchone()
        return row["to_status"] if row else None

    def _has_conflicting_operations(self, fixture_id, import_created_at, import_batch_no):
        rows = self.db.execute(
            "SELECT * FROM history WHERE fixture_id=? AND created_at>=? AND action IN (?,?,?,?,?,?) AND remark NOT LIKE ?",
            (fixture_id, import_created_at, "借出", "归还登记", "复核入库", "巡检冻结", "维修冻结", "报废", f"%批次 {import_batch_no}%"),
        ).fetchall()
        return [dict(r) for r in rows]

    def rollback_batch(self, batch_id, operator):
        if not operator:
            raise ValueError("操作人不能为空")

        batch = self.db.get_import_batch(batch_id)
        if not batch:
            raise ValueError("批次不存在")
        if batch["status"] != "completed":
            raise ValueError(f"批次状态为「{batch['status']}」，无法回滚")

        items = self.db.get_import_batch_items(batch_id)
        if not items:
            raise ValueError("批次没有明细记录")

        conflicts = []
        safe_items = []

        for item in items:
            if item["result"] not in (RESULT_NEW, RESULT_UPDATE):
                continue

            fixture_no = item["fixture_no"]
            current = self.db.get_fixture_by_no(fixture_no)

            if item["result"] == RESULT_NEW:
                if not current:
                    safe_items.append(("delete", item, None))
                    continue
                fid = current["id"]
                conflicting_ops = self._has_conflicting_operations(fid, batch["created_at"], batch["batch_no"])
                if conflicting_ops:
                    conflicts.append({
                        "fixture_no": fixture_no,
                        "item_id": item["id"],
                        "reason": f"新增后已发生操作: {[o['action'] for o in conflicting_ops]}",
                        "current_status": current["status"],
                    })
                else:
                    safe_items.append(("delete", item, current))

            elif item["result"] == RESULT_UPDATE:
                if not current:
                    conflicts.append({
                        "fixture_no": fixture_no,
                        "item_id": item["id"],
                        "reason": "灯具已被删除，无法回滚更新",
                        "current_status": "已删除",
                    })
                    continue

                if current["status"] not in ROLLBACK_SAFE_STATUSES:
                    conflicts.append({
                        "fixture_no": fixture_no,
                        "item_id": item["id"],
                        "reason": f"当前状态「{current['status']}」不允许回滚，仅「{STATUS_AVAILABLE}」可回滚",
                        "current_status": current["status"],
                    })
                    continue

                fid = current["id"]
                conflicting_ops = self._has_conflicting_operations(fid, batch["created_at"], batch["batch_no"])
                if conflicting_ops:
                    conflicts.append({
                        "fixture_no": fixture_no,
                        "item_id": item["id"],
                        "reason": f"更新后已发生操作: {[o['action'] for o in conflicting_ops]}",
                        "current_status": current["status"],
                    })
                else:
                    if not item["before_snapshot"]:
                        conflicts.append({
                            "fixture_no": fixture_no,
                            "item_id": item["id"],
                            "reason": "缺少变更前快照，无法安全回滚",
                            "current_status": current["status"],
                        })
                    else:
                        before = json.loads(item["before_snapshot"])
                        safe_items.append(("restore", item, current, before))

        if not safe_items:
            return {
                "rolled_back": 0,
                "conflicts": conflicts,
                "batch_no": batch["batch_no"],
            }

        self.db.begin_transaction()
        try:
            rollback_count = 0
            for action in safe_items:
                if action[0] == "delete":
                    _, item, current = action
                    if current:
                        fid = current["id"]
                        self.db.execute("DELETE FROM history WHERE fixture_id=?", (fid,))
                        self.db.execute("DELETE FROM fixtures WHERE id=?", (fid,))
                        self.db.execute(
                            "UPDATE import_batch_items SET result=?, error_message=? WHERE id=?",
                            (RESULT_SKIP, f"已回滚（删除新增），操作人: {operator}", item["id"]),
                        )
                    rollback_count += 1

                elif action[0] == "restore":
                    _, item, current, before = action
                    fid = current["id"]
                    old_status = current["status"]
                    new_status = before.get("status", STATUS_AVAILABLE)
                    self.db.execute(
                        "UPDATE fixtures SET model=?, accessories=?, location=?, inspection_due_date=?, "
                        "person_in_charge=?, status=?, last_remark=?, updated_at=datetime('now','localtime') WHERE id=?",
                        (
                            before.get("model", ""),
                            before.get("accessories", ""),
                            before.get("location", ""),
                            before.get("inspection_due_date", ""),
                            before.get("person_in_charge", ""),
                            new_status,
                            f"回滚到批次前状态，批次 {batch['batch_no']}",
                            fid,
                        ),
                    )
                    self.db.execute(
                        "INSERT INTO history (fixture_id,action,from_status,to_status,operator,remark) VALUES (?,?,?,?,?,?)",
                        (fid, "批次回滚", old_status, new_status, operator, f"回滚批次 {batch['batch_no']} 的更新"),
                    )
                    self.db.execute(
                        "UPDATE import_batch_items SET result=?, error_message=? WHERE id=?",
                        (RESULT_SKIP, f"已回滚（恢复原值），操作人: {operator}", item["id"]),
                    )
                    rollback_count += 1

            self.db.update_import_batch_status(
                batch_id,
                "rolled_back",
                f"已回滚 {rollback_count} 条，冲突 {len(conflicts)} 条: " +
                "; ".join(f"{c['fixture_no']}: {c['reason']}" for c in conflicts),
            )
            self.db.commit()

            return {
                "rolled_back": rollback_count,
                "conflicts": conflicts,
                "batch_no": batch["batch_no"],
            }

        except Exception as e:
            self.db.rollback_transaction()
            raise

    def get_import_batches(self):
        return self.db.get_import_batches()

    def get_import_batch_detail(self, batch_id):
        batch = self.db.get_import_batch(batch_id)
        if not batch:
            return None
        items = self.db.get_import_batch_items(batch_id)
        for item in items:
            if item["before_snapshot"]:
                item["before_obj"] = json.loads(item["before_snapshot"])
            else:
                item["before_obj"] = None
            if item["after_snapshot"]:
                item["after_obj"] = json.loads(item["after_snapshot"])
            else:
                item["after_obj"] = None
        return {"batch": batch, "items": items}

    def export_batch_errors(self, batch_id, directory, filename):
        batch = self.db.get_import_batch(batch_id)
        if not batch:
            raise ValueError("批次不存在")
        errors = self.db.get_import_batch_errors(batch_id)
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"目录 '{directory}' 不存在")
        if not os.access(directory, os.W_OK):
            raise PermissionError(f"目录 '{directory}' 不可写")
        filepath = dir_path / filename
        with open(filepath, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["行号", "灯具编号", "结果", "错误信息"])
            for e in errors:
                writer.writerow([e["row_index"], e["fixture_no"], e["result"], e["error_message"]])
        return str(filepath)

    def export_import_template(self, directory, filename, fmt="csv"):
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"目录 '{directory}' 不存在")
        if not os.access(directory, os.W_OK):
            raise PermissionError(f"目录 '{directory}' 不可写")
        filepath = dir_path / filename
        if fmt == "csv":
            with open(filepath, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(EXPORT_HEADERS)
                writer.writerow(["L001", "PAR64", "灯钩x1,电源线x1", "A-01", "2027-12-31", "张三", "可用", "示例记录-可删除"])
                writer.writerow(["L002", "LED200", "灯钩x1", "A-02", "2027-06-30", "李四", "", ""])
        elif fmt == "json":
            data = [
                {
                    "fixture_no": "L001",
                    "model": "PAR64",
                    "accessories": "灯钩x1,电源线x1",
                    "location": "A-01",
                    "inspection_due_date": "2027-12-31",
                    "person_in_charge": "张三",
                    "status": "可用",
                    "last_remark": "示例记录-可删除",
                },
                {
                    "fixture_no": "L002",
                    "model": "LED200",
                    "accessories": "灯钩x1",
                    "location": "A-02",
                    "inspection_due_date": "2027-06-30",
                    "person_in_charge": "李四",
                    "status": "",
                    "last_remark": "",
                },
            ]
            with open(filepath, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        else:
            raise ValueError(f"不支持的格式: {fmt}")
        return str(filepath)

    def _compute_file_hash(self, filepath):
        import hashlib
        try:
            h = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    def create_draft_from_precheck(self, filepath, operator, precheck_results, summary, filter_conditions=None, remark=""):
        if not operator:
            raise ValueError("操作人不能为空")

        source_name = Path(filepath).name
        file_hash = self._compute_file_hash(filepath)
        filter_json = json.dumps(filter_conditions or {}, ensure_ascii=False)

        draft_no = f"DFT{datetime.now().strftime('%Y%m%d%H%M%S')}{datetime.now().microsecond // 1000:03d}"

        self.db.begin_transaction()
        try:
            draft_id = self.db.create_draft_batch(
                draft_no, operator, source_name, file_hash, filter_json, remark,
                summary["total"],
                summary.get(RESULT_NEW, 0),
                summary.get(RESULT_UPDATE, 0),
                summary.get(RESULT_SKIP, 0),
                summary.get(RESULT_ERROR, 0),
            )

            for pr in precheck_results:
                row_idx = pr["row"]
                fixture_no = pr["fixture_no"]
                result = pr["result"]
                error_msg = "; ".join(pr["errors"]) if pr["errors"] else ""
                before_snap = json.dumps(pr["before"], ensure_ascii=False) if pr["before"] else ""
                after_snap = json.dumps(pr["after"], ensure_ascii=False) if pr["after"] else ""
                record_data = json.dumps(pr.get("record", {}), ensure_ascii=False)
                selected = 1 if result in (RESULT_NEW, RESULT_UPDATE) else 0

                self.db.add_draft_item(
                    draft_id, fixture_no, row_idx, result, error_msg,
                    before_snap, after_snap, record_data, selected, "", ""
                )

            self.db.commit()
            return {"draft_id": draft_id, "draft_no": draft_no}
        except Exception:
            self.db.rollback_transaction()
            raise

    def get_draft_batches(self):
        return self.db.get_draft_batches()

    def get_draft_detail(self, draft_id):
        draft = self.db.get_draft_batch(draft_id)
        if not draft:
            return None
        items = self.db.get_draft_items(draft_id)
        for item in items:
            if item["before_snapshot"]:
                item["before_obj"] = json.loads(item["before_snapshot"])
            else:
                item["before_obj"] = None
            if item["after_snapshot"]:
                item["after_obj"] = json.loads(item["after_snapshot"])
            else:
                item["after_obj"] = None
            if item["record_data"]:
                item["record_obj"] = json.loads(item["record_data"])
            else:
                item["record_obj"] = None
        return {"draft": draft, "items": items}

    def update_draft_remark(self, draft_id, remark):
        self.db.update_draft_batch(draft_id, remark=remark or "")

    def set_draft_item_selected(self, item_id, selected):
        self.db.set_draft_item_selected(item_id, selected)

    def set_all_draft_items_selected(self, draft_id, selected):
        self.db.set_all_draft_items_selected(draft_id, selected)

    def set_draft_items_selected_by_result(self, draft_id, result, selected):
        self.db.set_draft_items_selected_by_result(draft_id, result, selected)

    def delete_draft(self, draft_id):
        self.db.delete_draft_batch(draft_id)

    def _detect_draft_conflicts(self, draft_id, filepath=None):
        draft = self.db.get_draft_batch(draft_id)
        if not draft:
            raise ValueError("草稿批次不存在")

        items = self.db.get_selected_draft_items(draft_id)
        conflicts = []

        if filepath and draft["source_file_hash"]:
            current_hash = self._compute_file_hash(filepath)
            if current_hash and current_hash != draft["source_file_hash"]:
                conflicts.append({
                    "type": "file_changed",
                    "fixture_no": "",
                    "item_id": None,
                    "detail": f"源文件内容已变化（原哈希: {draft['source_file_hash'][:8]}..., 当前: {current_hash[:8]}...），草稿数据可能已过时",
                })

        for item in items:
            if item["result"] not in (RESULT_NEW, RESULT_UPDATE):
                continue

            fixture_no = item["fixture_no"]
            current = self.db.get_fixture_by_no(fixture_no)

            if item["result"] == RESULT_NEW:
                if current:
                    conflicts.append({
                        "type": "new_conflict",
                        "fixture_no": fixture_no,
                        "item_id": item["id"],
                        "detail": f"灯具已存在（当前状态: {current['status']}），不能再新增",
                    })
                    continue

            elif item["result"] == RESULT_UPDATE:
                if not current:
                    conflicts.append({
                        "type": "update_conflict_deleted",
                        "fixture_no": fixture_no,
                        "item_id": item["id"],
                        "detail": "灯具已被删除，无法更新",
                    })
                    continue

                if item["before_snapshot"]:
                    before = json.loads(item["before_snapshot"])
                    changed_fields = []
                    for field in ["status", "model", "accessories", "location", "inspection_due_date", "person_in_charge"]:
                        old_val = before.get(field, "")
                        cur_val = current.get(field, "")
                        if old_val != cur_val:
                            changed_fields.append(f"{field}: {old_val} → {cur_val}")
                    if changed_fields:
                        conflicts.append({
                            "type": "update_conflict_changed",
                            "fixture_no": fixture_no,
                            "item_id": item["id"],
                            "detail": f"灯具数据已变化: {'; '.join(changed_fields)}",
                        })
                        continue

                latest_batch = self.db.get_latest_batch_for_fixture(fixture_no)
                if latest_batch:
                    draft_created = draft["created_at"]
                    batch_created = latest_batch.get("created_at", "")
                    if batch_created and batch_created > draft_created:
                        conflicts.append({
                            "type": "other_batch_conflict",
                            "fixture_no": fixture_no,
                            "item_id": item["id"],
                            "detail": f"已被批次 {latest_batch['batch_no']}（{batch_created}）修改过",
                        })

        return conflicts

    def submit_draft(self, draft_id, operator, filepath=None):
        if not operator:
            raise ValueError("操作人不能为空")

        draft = self.db.get_draft_batch(draft_id)
        if not draft:
            raise ValueError("草稿批次不存在")
        if draft["status"] != "draft":
            raise ValueError(f"草稿状态为「{draft['status']}」，无法提交")

        conflicts = self._detect_draft_conflicts(draft_id, filepath)
        if conflicts:
            conflict_msgs = []
            for c in conflicts:
                if c["fixture_no"]:
                    conflict_msgs.append(f"{c['fixture_no']}: {c['detail']}")
                else:
                    conflict_msgs.append(c["detail"])
            err = ValueError("提交前检测到冲突:\n" + "\n".join(conflict_msgs))
            err.conflicts = conflicts
            raise err

        selected_items = self.db.get_selected_draft_items(draft_id)
        if not selected_items:
            raise ValueError("没有选中的记录可提交")

        error_items = [it for it in selected_items if it["result"] == RESULT_ERROR]
        if error_items:
            raise ValueError(f"选中的记录中有 {len(error_items)} 条错误记录，请先取消选中或修正后再提交")

        batch_no = f"IMP{datetime.now().strftime('%Y%m%d%H%M%S')}{datetime.now().microsecond // 1000:03d}"
        source_name = draft["source_file"]

        self.db.begin_transaction()
        try:
            batch_id = self.db.create_import_batch(batch_no, operator, source_name, len(selected_items))

            counts = {RESULT_NEW: 0, RESULT_UPDATE: 0, RESULT_SKIP: 0, RESULT_ERROR: 0}

            for item in selected_items:
                fixture_no = item["fixture_no"]
                result = item["result"]
                row_idx = item["row_index"]

                before = json.loads(item["before_snapshot"]) if item["before_snapshot"] else None
                after_obj = json.loads(item["after_snapshot"]) if item["after_snapshot"] else None
                record = json.loads(item["record_data"]) if item["record_data"] else {}

                before_snap = item["before_snapshot"]
                after_snap = ""
                error_msg = ""

                if result == RESULT_NEW:
                    status_val = (after_obj.get("status") if after_obj else None) or STATUS_AVAILABLE
                    fid = self.db.execute(
                        "INSERT INTO fixtures (fixture_no,model,accessories,location,inspection_due_date,person_in_charge,status,last_remark) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            fixture_no,
                            after_obj.get("model", "") if after_obj else "",
                            after_obj.get("accessories", "") if after_obj else "",
                            after_obj.get("location", "") if after_obj else "",
                            after_obj.get("inspection_due_date", "") if after_obj else "",
                            after_obj.get("person_in_charge", "") if after_obj else "",
                            status_val,
                            after_obj.get("last_remark", f"批量导入，批次 {batch_no}") if after_obj else f"批量导入，批次 {batch_no}",
                        ),
                    ).lastrowid
                    self.db.execute(
                        "INSERT INTO history (fixture_id,action,from_status,to_status,operator,remark) VALUES (?,?,?,?,?,?)",
                        (fid, "批量导入", "", status_val, operator, f"批次 {batch_no}"),
                    )
                    new_f = self.db.execute("SELECT * FROM fixtures WHERE id=?", (fid,)).fetchone()
                    after_snap = json.dumps(dict(new_f), ensure_ascii=False)
                    counts[RESULT_NEW] += 1

                elif result == RESULT_UPDATE:
                    fid = before["id"]
                    old_status = before["status"]
                    updates = {}
                    for field in ["model", "accessories", "location", "inspection_due_date", "person_in_charge", "status", "last_remark"]:
                        new_val = after_obj.get(field, "") if after_obj else ""
                        old_val = before.get(field, "")
                        if field == "status" and not new_val:
                            continue
                        if new_val and new_val != old_val:
                            updates[field] = new_val

                    if updates:
                        sets = ", ".join(f"{k}=?" for k in updates)
                        vals = list(updates.values()) + [fid]
                        self.db.execute(f"UPDATE fixtures SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", vals)
                        new_status = updates.get("status", old_status)
                        self.db.execute(
                            "INSERT INTO history (fixture_id,action,from_status,to_status,operator,remark) VALUES (?,?,?,?,?,?)",
                            (fid, "批量更新", old_status, new_status, operator, f"批次 {batch_no}"),
                        )
                    new_f = self.db.execute("SELECT * FROM fixtures WHERE id=?", (fid,)).fetchone()
                    after_snap = json.dumps(dict(new_f), ensure_ascii=False)
                    counts[RESULT_UPDATE] += 1

                elif result == RESULT_SKIP:
                    after_snap = before_snap
                    counts[RESULT_SKIP] += 1

                self.db.add_import_batch_item(
                    batch_id, fixture_no, row_idx, result, error_msg, before_snap, after_snap
                )

            self.db.update_import_batch_counts(
                batch_id, counts[RESULT_NEW], counts[RESULT_UPDATE], counts[RESULT_SKIP], counts[RESULT_ERROR]
            )
            self.db.update_import_batch_status(batch_id, "completed")

            self.db.update_draft_batch(draft_id, status="submitted")

            self.db.commit()

            return {
                "batch_id": batch_id,
                "batch_no": batch_no,
                "summary": {
                    "total": len(selected_items),
                    "new": counts[RESULT_NEW],
                    "update": counts[RESULT_UPDATE],
                    "skip": counts[RESULT_SKIP],
                    "error": counts[RESULT_ERROR],
                },
            }

        except Exception as e:
            self.db.rollback_transaction()
            raise

    def export_draft_items(self, draft_id, directory, filename, selected_only=False):
        draft = self.db.get_draft_batch(draft_id)
        if not draft:
            raise ValueError("草稿批次不存在")

        if selected_only:
            items = self.db.get_selected_draft_items(draft_id)
        else:
            items = self.db.get_draft_items(draft_id)

        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"目录 '{directory}' 不存在")
        if not os.access(directory, os.W_OK):
            raise PermissionError(f"目录 '{directory}' 不可写")

        filepath = dir_path / filename
        with open(filepath, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["行号", "灯具编号", "结果", "是否选中", "信息", "变更详情"])
            for item in items:
                detail_parts = []
                if item["before_snapshot"] and item["after_snapshot"]:
                    before = json.loads(item["before_snapshot"])
                    after = json.loads(item["after_snapshot"])
                    for field in ["model", "accessories", "location", "inspection_due_date", "person_in_charge", "status"]:
                        old_val = before.get(field, "")
                        new_val = after.get(field, "")
                        if field == "status" and not new_val:
                            continue
                        if new_val and new_val != old_val:
                            detail_parts.append(f"{field}: {old_val} → {new_val}")
                detail = "; ".join(detail_parts)
                selected_str = "是" if item["selected"] else "否"
                msg = item["error_message"] or (item["result"] == RESULT_NEW and "新增") or (item["result"] == RESULT_UPDATE and "更新") or (item["result"] == RESULT_SKIP and "跳过（无变化）") or ""
                writer.writerow([item["row_index"], item["fixture_no"], item["result"], selected_str, msg, detail])
        return str(filepath)


class FormDialog(tk.Toplevel):
    def __init__(self, parent, title, fields, initial=None, width=360):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.entries = {}
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        for i, (key, label, default) in enumerate(fields):
            ttk.Label(main, text=label + ":").grid(row=i, column=0, sticky="e", padx=(0, 8), pady=4)
            var = tk.StringVar(value=str(default) if default else "")
            if key in ("inspection_due_date", "new_due_date"):
                entry = ttk.Entry(main, textvariable=var, width=24)
                hint = ttk.Label(main, text="YYYY-MM-DD", foreground="gray")
                hint.grid(row=i, column=2, padx=(4, 0), pady=4)
            elif key == "accessories":
                entry = tk.Text(main, width=28, height=3)
                if default:
                    entry.insert("1.0", str(default))
            else:
                entry = ttk.Entry(main, textvariable=var, width=28)
            entry.grid(row=i, column=1, sticky="ew", pady=4)
            self.entries[key] = (entry, var, key == "accessories")

        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=len(fields), column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btn_frame, text="确定", command=self._ok, width=10).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="取消", command=self._cancel, width=10).pack(side="left", padx=4)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.geometry(f"{width}x{min(60 + len(fields) * 44, 500)}")
        self.wait_window()

    def _ok(self):
        self.result = {}
        for key, (widget, var, is_text) in self.entries.items():
            if is_text:
                self.result[key] = widget.get("1.0", "end").strip()
            else:
                self.result[key] = var.get().strip()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class PrecheckDialog(tk.Toplevel):
    def __init__(self, parent, filepath, precheck_results, summary):
        super().__init__(parent)
        self.title(f"导入预检 - {Path(filepath).name}")
        self.result = None
        self.precheck_results = precheck_results
        self.summary = summary
        self.filepath = filepath
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        summary_frame = ttk.LabelFrame(main, text="预检汇总", padding=8)
        summary_frame.pack(fill="x", pady=(0, 8))

        sum_text = (
            f"共 {summary['total']} 条记录\n"
            f"  新增: {summary[RESULT_NEW]} 条    "
            f"更新: {summary[RESULT_UPDATE]} 条    "
            f"跳过: {summary[RESULT_SKIP]} 条    "
            f"错误: {summary[RESULT_ERROR]} 条"
        )
        ttk.Label(summary_frame, text=sum_text, font=("Arial", 10, "bold")).pack(anchor="w")

        cols = ("row", "fixture_no", "result", "detail")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=15)
        self.tree.heading("row", text="行号", anchor="center")
        self.tree.heading("fixture_no", text="灯具编号", anchor="center")
        self.tree.heading("result", text="结果", anchor="center")
        self.tree.heading("detail", text="详情", anchor="w")
        self.tree.column("row", width=60, anchor="center")
        self.tree.column("fixture_no", width=100, anchor="center")
        self.tree.column("result", width=80, anchor="center")
        self.tree.column("detail", width=480, anchor="w")

        result_tags = {
            RESULT_NEW: ("new", "#1565c0"),
            RESULT_UPDATE: ("update", "#2e7d32"),
            RESULT_SKIP: ("skip", "#f57f17"),
            RESULT_ERROR: ("error", "#c62828"),
        }
        for tag, color in result_tags.values():
            self.tree.tag_configure(tag, foreground=color)

        for r in precheck_results:
            detail_parts = []
            if r["errors"]:
                detail_parts.append("错误: " + "; ".join(r["errors"]))
            if r["warnings"]:
                detail_parts.append("提示: " + "; ".join(r["warnings"]))
            if not detail_parts:
                if r["result"] == RESULT_NEW:
                    detail_parts.append("将新增")
                elif r["result"] == RESULT_UPDATE:
                    changes = []
                    for f in ["model", "accessories", "location", "inspection_due_date", "person_in_charge", "status"]:
                        old = r["before"].get(f, "") if r["before"] else ""
                        new = r["after"].get(f, "") if r["after"] else ""
                        if f == "status" and not new:
                            continue
                        if new and new != old:
                            changes.append(f"{f}: {old} → {new}")
                    detail_parts.append("更新: " + "; ".join(changes))
                elif r["result"] == RESULT_SKIP:
                    detail_parts.append("数据无变化")
            detail = " | ".join(detail_parts)
            tag_name, _ = result_tags.get(r["result"], ("", ""))
            self.tree.insert("", "end", values=(r["row"], r["fixture_no"], r["result"], detail), tags=(tag_name,))

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        op_frame = ttk.Frame(main)
        op_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(op_frame, text="操作人:").pack(side="left", padx=(0, 4))
        self.operator_var = tk.StringVar()
        ttk.Entry(op_frame, textvariable=self.operator_var, width=20).pack(side="left")
        ttk.Label(op_frame, text="  备注:").pack(side="left", padx=(8, 4))
        self.remark_var = tk.StringVar()
        ttk.Entry(op_frame, textvariable=self.remark_var, width=30).pack(side="left")

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(10, 0))

        if summary[RESULT_ERROR] > 0:
            ttk.Label(btn_frame, text=f"⚠️  存在 {summary[RESULT_ERROR]} 条错误，可保存为草稿后续处理", foreground="#c62828").pack(side="left", padx=4)
            ttk.Button(btn_frame, text="取消", command=self._cancel, width=10).pack(side="right")
            ttk.Button(btn_frame, text="保存为草稿", command=self._on_save_draft, width=12).pack(side="right", padx=4)
        else:
            ttk.Button(btn_frame, text="取消", command=self._cancel, width=10).pack(side="right")
            ttk.Button(btn_frame, text="直接导入", command=self._ok, width=10).pack(side="right", padx=4)
            ttk.Button(btn_frame, text="保存为草稿", command=self._on_save_draft, width=12).pack(side="right", padx=4)

        self.geometry("800x600")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window()

    def _ok(self):
        self.result = {"action": "import", "operator": self.operator_var.get().strip()}
        self.destroy()

    def _on_save_draft(self):
        self.result = {
            "action": "save_draft",
            "operator": self.operator_var.get().strip(),
            "remark": self.remark_var.get().strip(),
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class BatchHistoryDialog(tk.Toplevel):
    def __init__(self, parent, service):
        super().__init__(parent)
        self.title("导入批次历史")
        self.result = None
        self.service = service
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        cols = ("batch_no", "created_at", "operator", "source_file", "total",
                "new_count", "update_count", "skip_count", "error_count", "status")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=12)
        headers = [
            ("batch_no", "批次号", 140),
            ("created_at", "导入时间", 150),
            ("operator", "操作人", 80),
            ("source_file", "来源文件", 180),
            ("total", "总数", 50),
            ("new_count", "新增", 50),
            ("update_count", "更新", 50),
            ("skip_count", "跳过", 50),
            ("error_count", "错误", 50),
            ("status", "状态", 80),
        ]
        for col_id, col_name, col_w in headers:
            self.tree.heading(col_id, text=col_name, anchor="center")
            self.tree.column(col_id, width=col_w, anchor="center")

        self.tree.tag_configure("completed", foreground="#2e7d32")
        self.tree.tag_configure("rolled_back", foreground="#757575")
        self.tree.tag_configure("error", foreground="#c62828")
        self.tree.tag_configure("failed", foreground="#c62828")

        self._refresh_batches()
        self.tree.bind("<<TreeviewSelect>>", self._on_select_batch)

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        detail_frame = ttk.LabelFrame(main, text="批次明细", padding=8)
        detail_frame.pack(fill="both", expand=True, pady=(8, 0))

        dcols = ("row", "fixture_no", "result", "error_message")
        self.detail_tree = ttk.Treeview(detail_frame, columns=dcols, show="headings", height=8)
        dheaders = [
            ("row", "行号", 60),
            ("fixture_no", "灯具编号", 100),
            ("result", "结果", 80),
            ("error_message", "信息", 420),
        ]
        for col_id, col_name, col_w in dheaders:
            self.detail_tree.heading(col_id, text=col_name, anchor="center")
            self.detail_tree.column(col_id, width=col_w, anchor="w")

        self.detail_tree.tag_configure("new", foreground="#1565c0")
        self.detail_tree.tag_configure("update", foreground="#2e7d32")
        self.detail_tree.tag_configure("skip", foreground="#f57f17")
        self.detail_tree.tag_configure("error", foreground="#c62828")

        dvsb = ttk.Scrollbar(detail_frame, orient="vertical", command=self.detail_tree.yview)
        self.detail_tree.configure(yscrollcommand=dvsb.set)
        self.detail_tree.pack(side="left", fill="both", expand=True)
        dvsb.pack(side="right", fill="y")

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(10, 0))

        self.btn_rollback = ttk.Button(btn_frame, text="撤销此批次", command=self._on_rollback, width=14, state="disabled")
        self.btn_rollback.pack(side="left", padx=4)

        self.btn_export_errors = ttk.Button(btn_frame, text="导出错误清单", command=self._on_export_errors, width=14, state="disabled")
        self.btn_export_errors.pack(side="left", padx=4)

        self.detail_label = ttk.Label(btn_frame, text="")
        self.detail_label.pack(side="left", padx=10)

        ttk.Button(btn_frame, text="关闭", command=self._cancel, width=12).pack(side="right")

        self.geometry("900x700")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window()

    def _refresh_batches(self):
        self.tree.delete(*self.tree.get_children())
        batches = self.service.get_import_batches()
        for b in batches:
            tag = b["status"] if b["status"] in ("completed", "rolled_back", "failed") else "error"
            status_display = {
                "pending": "处理中",
                "completed": "已完成",
                "rolled_back": "已回滚",
                "failed": "预检失败",
                "error": "错误",
            }.get(b["status"], b["status"])
            self.tree.insert("", "end", iid=str(b["id"]), values=(
                b["batch_no"], b["created_at"], b["operator"], b["source_file"],
                b["record_count"], b["new_count"], b["update_count"],
                b["skip_count"], b["error_count"], status_display
            ), tags=(tag,))

    def _on_select_batch(self, event=None):
        sel = self.tree.selection()
        self.detail_tree.delete(*self.detail_tree.get_children())
        if not sel:
            self.btn_rollback.config(state="disabled")
            self.btn_export_errors.config(state="disabled")
            self.detail_label.config(text="")
            return

        batch_id = int(sel[0])
        detail = self.service.get_import_batch_detail(batch_id)
        if not detail:
            return

        batch = detail["batch"]
        can_rollback = batch["status"] == "completed"
        has_errors = batch["error_count"] > 0
        self.btn_rollback.config(state="normal" if can_rollback else "disabled")
        self.btn_export_errors.config(state="normal" if has_errors else "disabled")

        self.detail_label.config(
            text=f"批次 {batch['batch_no']} | 总数 {batch['record_count']} | "
                 f"新增 {batch['new_count']} 更新 {batch['update_count']} "
                 f"跳过 {batch['skip_count']} 错误 {batch['error_count']}"
        )

        for item in detail["items"]:
            tag = item["result"]
            msg = item["error_message"]
            if not msg and item["result"] == RESULT_NEW:
                msg = "新增成功"
            elif not msg and item["result"] == RESULT_UPDATE:
                msg = "更新成功"
            elif not msg and item["result"] == RESULT_SKIP:
                msg = "跳过（无变化）"
            self.detail_tree.insert("", "end", values=(
                item["row_index"], item["fixture_no"], item["result"], msg
            ), tags=(tag,))

    def _on_rollback(self):
        sel = self.tree.selection()
        if not sel:
            return
        batch_id = int(sel[0])
        detail = self.service.get_import_batch_detail(batch_id)
        if not detail or detail["batch"]["status"] != "completed":
            messagebox.showerror("错误", "此批次无法回滚")
            return
        if not messagebox.askyesno("确认回滚", f"确定要撤销批次 {detail['batch']['batch_no']} 吗？\n这将回滚该批次的新增和更新记录。"):
            return
        self.result = {"action": "rollback", "batch_id": batch_id}
        self.destroy()

    def _on_export_errors(self):
        sel = self.tree.selection()
        if not sel:
            return
        batch_id = int(sel[0])
        self.result = {"action": "export_errors", "batch_id": batch_id}
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class DraftListDialog(tk.Toplevel):
    def __init__(self, parent, service):
        super().__init__(parent)
        self.title("导入草稿批次")
        self.result = None
        self.service = service
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        cols = ("draft_no", "created_at", "updated_at", "operator", "source_file",
                "total", "new_count", "update_count", "skip_count", "error_count", "remark")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=12)
        headers = [
            ("draft_no", "草稿号", 140),
            ("created_at", "创建时间", 140),
            ("updated_at", "更新时间", 140),
            ("operator", "操作人", 80),
            ("source_file", "来源文件", 160),
            ("total", "总数", 50),
            ("new_count", "新增", 50),
            ("update_count", "更新", 50),
            ("skip_count", "跳过", 50),
            ("error_count", "错误", 50),
            ("remark", "备注", 200),
        ]
        for col_id, col_name, col_w in headers:
            self.tree.heading(col_id, text=col_name, anchor="center")
            self.tree.column(col_id, width=col_w, anchor="center")

        self._refresh_drafts()
        self.tree.bind("<<TreeviewSelect>>", self._on_select_draft)

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(10, 0))

        ttk.Button(btn_frame, text="新建草稿...", command=self._on_new, width=14).pack(side="left", padx=4)
        self.btn_edit = ttk.Button(btn_frame, text="编辑草稿", command=self._on_edit, width=12, state="disabled")
        self.btn_edit.pack(side="left", padx=4)
        self.btn_submit = ttk.Button(btn_frame, text="提交选中", command=self._on_submit, width=12, state="disabled")
        self.btn_submit.pack(side="left", padx=4)
        self.btn_delete = ttk.Button(btn_frame, text="删除草稿", command=self._on_delete, width=12, state="disabled")
        self.btn_delete.pack(side="left", padx=4)
        self.btn_export = ttk.Button(btn_frame, text="导出明细", command=self._on_export, width=12, state="disabled")
        self.btn_export.pack(side="left", padx=4)

        ttk.Button(btn_frame, text="关闭", command=self._cancel, width=12).pack(side="right")

        self.geometry("1050x550")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window()

    def _refresh_drafts(self):
        self.tree.delete(*self.tree.get_children())
        drafts = self.service.get_draft_batches()
        for d in drafts:
            self.tree.insert("", "end", iid=str(d["id"]), values=(
                d["draft_no"], d["created_at"], d["updated_at"],
                d["operator"], d["source_file"],
                d["record_count"], d["new_count"], d["update_count"],
                d["skip_count"], d["error_count"], d["remark"]
            ))

    def _on_select_draft(self, event=None):
        sel = self.tree.selection()
        has_sel = bool(sel)
        self.btn_edit.config(state="normal" if has_sel else "disabled")
        self.btn_submit.config(state="normal" if has_sel else "disabled")
        self.btn_delete.config(state="normal" if has_sel else "disabled")
        self.btn_export.config(state="normal" if has_sel else "disabled")

    def _on_new(self):
        self.result = {"action": "new"}
        self.destroy()

    def _on_edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        draft_id = int(sel[0])
        self.result = {"action": "edit", "draft_id": draft_id}
        self.destroy()

    def _on_submit(self):
        sel = self.tree.selection()
        if not sel:
            return
        draft_id = int(sel[0])
        self.result = {"action": "submit", "draft_id": draft_id}
        self.destroy()

    def _on_delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        draft_id = int(sel[0])
        draft = self.service.db.get_draft_batch(draft_id)
        if not draft:
            return
        if not messagebox.askyesno("确认删除", f"确定要删除草稿 {draft['draft_no']} 吗？\n删除后不可恢复。"):
            return
        try:
            self.service.delete_draft(draft_id)
            self._refresh_drafts()
            messagebox.showinfo("成功", "草稿已删除")
        except Exception as e:
            messagebox.showerror("删除失败", str(e))

    def _on_export(self):
        sel = self.tree.selection()
        if not sel:
            return
        draft_id = int(sel[0])
        self.result = {"action": "export", "draft_id": draft_id}
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class DraftEditDialog(tk.Toplevel):
    def __init__(self, parent, service, draft_id, source_filepath=None):
        super().__init__(parent)
        self.title("编辑导入草稿")
        self.result = None
        self.service = service
        self.draft_id = draft_id
        self.source_filepath = source_filepath
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self.detail = self.service.get_draft_detail(draft_id)
        if not self.detail:
            messagebox.showerror("错误", "草稿不存在")
            self.destroy()
            return

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        info_frame = ttk.LabelFrame(main, text="草稿信息", padding=8)
        info_frame.pack(fill="x", pady=(0, 8))

        draft = self.detail["draft"]
        info_text = (
            f"草稿号: {draft['draft_no']}    "
            f"操作人: {draft['operator']}    "
            f"来源文件: {draft['source_file']}\n"
            f"创建时间: {draft['created_at']}    "
            f"更新时间: {draft['updated_at']}"
        )
        ttk.Label(info_frame, text=info_text, justify="left").pack(anchor="w")

        remark_frame = ttk.Frame(info_frame)
        remark_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(remark_frame, text="备注:").pack(side="left", padx=(0, 4))
        self.remark_var = tk.StringVar(value=draft.get("remark", ""))
        self.remark_entry = ttk.Entry(remark_frame, textvariable=self.remark_var, width=60)
        self.remark_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(remark_frame, text="保存备注", command=self._on_save_remark, width=10).pack(side="left", padx=8)

        filter_frame = ttk.LabelFrame(main, text="快捷选择", padding=8)
        filter_frame.pack(fill="x", pady=(0, 8))

        ttk.Button(filter_frame, text="全选", command=lambda: self._set_all_selected(True), width=8).pack(side="left", padx=2)
        ttk.Button(filter_frame, text="全不选", command=lambda: self._set_all_selected(False), width=8).pack(side="left", padx=2)
        ttk.Separator(filter_frame, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(filter_frame, text="按结果类型:").pack(side="left", padx=(0, 4))
        ttk.Button(filter_frame, text=f"选全部新增", command=lambda: self._set_by_result(RESULT_NEW, True), width=10).pack(side="left", padx=2)
        ttk.Button(filter_frame, text=f"取消新增", command=lambda: self._set_by_result(RESULT_NEW, False), width=10).pack(side="left", padx=2)
        ttk.Button(filter_frame, text=f"选全部更新", command=lambda: self._set_by_result(RESULT_UPDATE, True), width=10).pack(side="left", padx=2)
        ttk.Button(filter_frame, text=f"取消更新", command=lambda: self._set_by_result(RESULT_UPDATE, False), width=10).pack(side="left", padx=2)
        ttk.Button(filter_frame, text=f"选全部跳过", command=lambda: self._set_by_result(RESULT_SKIP, True), width=10).pack(side="left", padx=2)
        ttk.Button(filter_frame, text=f"取消跳过", command=lambda: self._set_by_result(RESULT_SKIP, False), width=10).pack(side="left", padx=2)

        cols = ("selected", "row", "fixture_no", "result", "detail")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=18)
        self.tree.heading("selected", text="选中", anchor="center")
        self.tree.heading("row", text="行号", anchor="center")
        self.tree.heading("fixture_no", text="灯具编号", anchor="center")
        self.tree.heading("result", text="结果", anchor="center")
        self.tree.heading("detail", text="详情", anchor="w")
        self.tree.column("selected", width=50, anchor="center")
        self.tree.column("row", width=60, anchor="center")
        self.tree.column("fixture_no", width=100, anchor="center")
        self.tree.column("result", width=80, anchor="center")
        self.tree.column("detail", width=520, anchor="w")

        result_tags = {
            RESULT_NEW: ("new", "#1565c0"),
            RESULT_UPDATE: ("update", "#2e7d32"),
            RESULT_SKIP: ("skip", "#f57f17"),
            RESULT_ERROR: ("error", "#c62828"),
        }
        for tag, color in result_tags.values():
            self.tree.tag_configure(tag, foreground=color)

        self.tree.bind("<Button-1>", self._on_tree_click)

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._refresh_items()

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(10, 0))

        self.summary_label = ttk.Label(btn_frame, text="")
        self.summary_label.pack(side="left", padx=4)

        ttk.Button(btn_frame, text="检测冲突", command=self._on_check_conflicts, width=12).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="提交选中记录", command=self._on_submit, width=14).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="关闭", command=self._cancel, width=10).pack(side="right")

        self._update_summary()

        self.geometry("950x700")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window()

    def _refresh_items(self):
        self.tree.delete(*self.tree.get_children())
        self.detail = self.service.get_draft_detail(self.draft_id)
        items = self.detail["items"]
        for item in items:
            selected_str = "☑" if item["selected"] else "☐"
            detail_parts = []
            if item["result"] == RESULT_ERROR:
                detail_parts.append("错误: " + item["error_message"])
            elif item["result"] == RESULT_NEW:
                detail_parts.append("将新增")
            elif item["result"] == RESULT_UPDATE:
                changes = []
                before = item["before_obj"] or {}
                after = item["after_obj"] or {}
                for f in ["model", "accessories", "location", "inspection_due_date", "person_in_charge", "status"]:
                    old = before.get(f, "")
                    new = after.get(f, "")
                    if f == "status" and not new:
                        continue
                    if new and new != old:
                        changes.append(f"{f}: {old} → {new}")
                detail_parts.append("更新: " + "; ".join(changes))
            elif item["result"] == RESULT_SKIP:
                detail_parts.append("数据无变化")
            detail = " | ".join(detail_parts)
            tag_name = item["result"]
            self.tree.insert("", "end", iid=str(item["id"]),
                             values=(selected_str, item["row_index"], item["fixture_no"], item["result"], detail),
                             tags=(tag_name,))

    def _update_summary(self):
        counts = self.service.db.count_draft_items_by_result(self.draft_id)
        total_sel = sum(c["selected"] for c in counts.values())
        total_all = sum(c["total"] for c in counts.values())
        text = f"总计 {total_all} 条，已选中 {total_sel} 条"
        parts = []
        for r in [RESULT_NEW, RESULT_UPDATE, RESULT_SKIP, RESULT_ERROR]:
            c = counts.get(r, {"total": 0, "selected": 0})
            if c["total"] > 0:
                parts.append(f"{r}: {c['selected']}/{c['total']}")
        if parts:
            text += " （" + "  ".join(parts) + "）"
        self.summary_label.config(text=text)

    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != "#1":
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        item_id = int(row_id)
        item = self.service.db.execute("SELECT selected FROM import_draft_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            return
        new_selected = 0 if item["selected"] else 1
        self.service.set_draft_item_selected(item_id, new_selected)
        self._refresh_items()
        self._update_summary()

    def _set_all_selected(self, selected):
        self.service.set_all_draft_items_selected(self.draft_id, selected)
        self._refresh_items()
        self._update_summary()

    def _set_by_result(self, result, selected):
        self.service.set_draft_items_selected_by_result(self.draft_id, result, selected)
        self._refresh_items()
        self._update_summary()

    def _on_save_remark(self):
        remark = self.remark_var.get().strip()
        try:
            self.service.update_draft_remark(self.draft_id, remark)
            messagebox.showinfo("成功", "备注已保存")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _on_check_conflicts(self):
        try:
            conflicts = self.service._detect_draft_conflicts(self.draft_id, self.source_filepath)
            if not conflicts:
                messagebox.showinfo("冲突检测", "未检测到冲突，选中的记录可以安全提交")
            else:
                conflict_msgs = []
                for c in conflicts:
                    if c["fixture_no"]:
                        conflict_msgs.append(f"{c['fixture_no']}: {c['detail']}")
                    else:
                        conflict_msgs.append(c["detail"])
                messagebox.showwarning("检测到冲突",
                                       f"发现 {len(conflicts)} 个冲突:\n\n" + "\n".join(conflict_msgs) +
                                       "\n\n请处理冲突后再提交。")
        except Exception as e:
            messagebox.showerror("检测失败", str(e))

    def _on_submit(self):
        selected = self.service.db.get_selected_draft_items(self.draft_id)
        if not selected:
            messagebox.showwarning("提示", "没有选中的记录可提交")
            return
        error_items = [it for it in selected if it["result"] == RESULT_ERROR]
        if error_items:
            messagebox.showerror("错误", f"选中的记录中有 {len(error_items)} 条错误记录，请先取消选中或修正后再提交")
            return

        if not messagebox.askyesno("确认提交",
                                    f"确定要提交选中的 {len(selected)} 条记录吗？\n\n"
                                    f"新增: {sum(1 for it in selected if it['result']==RESULT_NEW)} 条\n"
                                    f"更新: {sum(1 for it in selected if it['result']==RESULT_UPDATE)} 条\n"
                                    f"跳过: {sum(1 for it in selected if it['result']==RESULT_SKIP)} 条"):
            return

        fields = [("operator", "操作人", self.detail["draft"]["operator"])]
        dlg = FormDialog(self, "提交草稿", fields)
        if dlg.result is None:
            return
        operator = dlg.result.get("operator", "").strip()
        if not operator:
            messagebox.showerror("错误", "操作人不能为空")
            return

        try:
            result = self.service.submit_draft(self.draft_id, operator, self.source_filepath)
            self.result = {"action": "submitted", "batch_id": result["batch_id"], "batch_no": result["batch_no"], "summary": result["summary"]}
            self.destroy()
        except ValueError as e:
            messagebox.showerror("提交失败", str(e))
        except Exception as e:
            messagebox.showerror("提交失败", f"提交时发生错误:\n{e}")

    def _cancel(self):
        self.result = None
        self.destroy()


class Application(tk.Tk):
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.title(APP_TITLE)
        self.geometry("1100x720")
        self.minsize(900, 600)

        self._current_filters = {}
        self._selected_id = None

        self._build_menu()
        self._build_filter_panel()
        self._build_table()
        self._build_bottom_panel()
        self._build_status_bar()
        self._restore_filters_from_db()
        self._apply_filters_to_controls()
        self._refresh_table()
        self._update_status_bar()

    def _restore_filters_from_db(self):
        loc = self.service.db.get_setting(SETTING_FILTER_LOCATION, "")
        sta = self.service.db.get_setting(SETTING_FILTER_STATUS, "")
        ds = self.service.db.get_setting(SETTING_FILTER_DUE_START, "")
        de = self.service.db.get_setting(SETTING_FILTER_DUE_END, "")
        self._current_filters = {
            "location": loc or None,
            "status": sta or None,
            "due_start": ds or None,
            "due_end": de or None,
        }

    def _persist_filters_to_db(self):
        cf = self._current_filters
        self.service.db.set_setting(SETTING_FILTER_LOCATION, cf.get("location") or "")
        self.service.db.set_setting(SETTING_FILTER_STATUS, cf.get("status") or "")
        self.service.db.set_setting(SETTING_FILTER_DUE_START, cf.get("due_start") or "")
        self.service.db.set_setting(SETTING_FILTER_DUE_END, cf.get("due_end") or "")

    def _apply_filters_to_controls(self):
        cf = self._current_filters
        self.filter_location.set(cf.get("location") or "")
        self.filter_status.set(cf.get("status") or "")
        self.filter_due_start.delete(0, "end")
        if cf.get("due_start"):
            self.filter_due_start.insert(0, cf["due_start"])
        self.filter_due_end.delete(0, "end")
        if cf.get("due_end"):
            self.filter_due_end.insert(0, cf["due_end"])

    # ---- Menu ----

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="导出 CSV (当前筛选)...", command=self._on_export_csv)
        file_menu.add_command(label="导出 JSON (当前筛选)...", command=self._on_export_json)
        file_menu.add_separator()
        file_menu.add_command(label="导出导入模板 (CSV)...", command=self._on_export_template_csv)
        file_menu.add_command(label="导出导入模板 (JSON)...", command=self._on_export_template_json)
        file_menu.add_separator()
        file_menu.add_command(label="设置常用导出目录...", command=self._on_set_export_dir)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)

        action_menu = tk.Menu(menubar, tearoff=0)
        action_menu.add_command(label="添加灯具", command=self._on_add_fixture)
        action_menu.add_command(label="编辑灯具信息", command=self._on_edit_fixture)
        action_menu.add_command(label="删除灯具", command=self._on_delete_fixture)
        action_menu.add_separator()
        action_menu.add_command(label="批量导入灯具台账...", command=self._on_batch_import)
        action_menu.add_command(label="导入草稿批次...", command=self._on_draft_list)
        action_menu.add_command(label="导入批次历史...", command=self._on_batch_history)
        menubar.add_cascade(label="操作", menu=action_menu)

        self.config(menu=menubar)

    # ---- Filter Panel ----

    def _build_filter_panel(self):
        frame = ttk.LabelFrame(self, text="筛选", padding=6)
        frame.pack(fill="x", padx=8, pady=(8, 0))

        ttk.Label(frame, text="库位:").grid(row=0, column=0, padx=(0, 4))
        self.filter_location = ttk.Combobox(frame, width=12, values=[""] + self.service.get_all_locations())
        self.filter_location.grid(row=0, column=1, padx=(0, 12))

        ttk.Label(frame, text="状态:").grid(row=0, column=2, padx=(0, 4))
        self.filter_status = ttk.Combobox(frame, width=12, values=[""] + ALL_STATUSES)
        self.filter_status.grid(row=0, column=3, padx=(0, 12))

        ttk.Label(frame, text="到期起始:").grid(row=0, column=4, padx=(0, 4))
        self.filter_due_start = ttk.Entry(frame, width=11)
        self.filter_due_start.grid(row=0, column=5, padx=(0, 12))

        ttk.Label(frame, text="到期结束:").grid(row=0, column=6, padx=(0, 4))
        self.filter_due_end = ttk.Entry(frame, width=11)
        self.filter_due_end.grid(row=0, column=7, padx=(0, 12))

        ttk.Button(frame, text="筛选", command=self._on_filter).grid(row=0, column=8, padx=4)
        ttk.Button(frame, text="重置", command=self._on_reset_filter).grid(row=0, column=9, padx=4)

    # ---- Table ----

    def _build_table(self):
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=6)

        cols = [c[0] for c in TABLE_COLUMNS]
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")

        for col_id, col_name, col_w in TABLE_COLUMNS:
            self.tree.heading(col_id, text=col_name, anchor="center")
            self.tree.column(col_id, width=col_w, minwidth=40, anchor="center")

        for status_name in ALL_STATUSES:
            self.tree.tag_configure(status_name, foreground=STATUS_COLORS.get(status_name, "black"))

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    # ---- Bottom Panel ----

    def _build_bottom_panel(self):
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=(0, 4))

        action_frame = ttk.LabelFrame(bottom, text="操作", padding=6)
        action_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))

        buttons = [
            ("借出", self._on_borrow),
            ("归还登记", self._on_return),
            ("复核入库", self._on_review),
            ("巡检冻结", self._on_freeze_inspection),
            ("巡检解冻", self._on_unfreeze_inspection),
            ("维修冻结", self._on_freeze_maintenance),
            ("维修解冻", self._on_unfreeze_maintenance),
            ("报废", self._on_scrap),
        ]
        self.action_buttons = {}
        for i, (text, cmd) in enumerate(buttons):
            r, c = divmod(i, 4)
            btn = ttk.Button(action_frame, text=text, command=cmd, width=12)
            btn.grid(row=r, column=c, padx=3, pady=2, sticky="ew")
            self.action_buttons[text] = btn
        for c in range(4):
            action_frame.columnconfigure(c, weight=1)

        info_frame = ttk.LabelFrame(action_frame, text="选中灯具信息", padding=4)
        info_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        self.info_label = ttk.Label(info_frame, text="请选择灯具", wraplength=500, justify="left")
        self.info_label.pack(fill="x")

        hist_frame = ttk.LabelFrame(bottom, text="历史时间线", padding=6)
        hist_frame.pack(side="right", fill="both", expand=True, padx=(4, 0))

        self.hist_tree = ttk.Treeview(hist_frame, columns=("time", "action", "change", "operator", "remark"),
                                       show="headings", height=5)
        hist_cols = [("time", "时间", 140), ("action", "操作", 80), ("change", "状态变更", 120),
                     ("operator", "操作人", 70), ("remark", "备注", 160)]
        for col_id, col_name, col_w in hist_cols:
            self.hist_tree.heading(col_id, text=col_name, anchor="center")
            self.hist_tree.column(col_id, width=col_w, minwidth=40, anchor="center")

        hsb = ttk.Scrollbar(hist_frame, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=hsb.set)
        self.hist_tree.pack(side="left", fill="both", expand=True)
        hsb.pack(side="right", fill="y")

    # ---- Status Bar ----

    def _build_status_bar(self):
        self.status_bar = ttk.Label(self, text="", relief="sunken", anchor="w", padding=(8, 2))
        self.status_bar.pack(fill="x", side="bottom")

    # ---- Data Operations ----

    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        fixtures = self.service.get_fixtures(**self._current_filters)
        for f in fixtures:
            vals = [f.get(c, "") for c, _, _ in TABLE_COLUMNS]
            self.tree.insert("", "end", iid=str(f["id"]), values=vals, tags=(f["status"],))
        self._update_status_bar()
        self._refresh_locations()

    def _after_action(self, fixture_id):
        self._refresh_table()
        iid = str(fixture_id)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)
            self.tree.focus(iid)
        self._on_select()

    def _refresh_locations(self):
        locs = self.service.get_all_locations()
        self.filter_location["values"] = [""] + locs

    def _update_status_bar(self):
        counts = self.service.get_status_counts()
        parts = [f"{s}: {counts.get(s, 0)}" for s in ALL_STATUSES]
        total = sum(counts.values())
        self.status_bar.config(text=f"{'  |  '.join(parts)}  |  共计: {total}")

    def _update_action_buttons(self, status):
        action_status_map = {
            "借出": STATUS_AVAILABLE,
            "归还登记": STATUS_BORROWED,
            "复核入库": STATUS_RETURN_PENDING,
            "巡检冻结": None,
            "巡检解冻": STATUS_INSPECTION_FREEZE,
            "维修冻结": None,
            "维修解冻": STATUS_MAINTENANCE_FREEZE,
            "报废": None,
        }
        for text, btn in self.action_buttons.items():
            required = action_status_map.get(text)
            if required is None:
                allowed = TRANSITIONS.get(status, [])
                target = {
                    "巡检冻结": STATUS_INSPECTION_FREEZE,
                    "维修冻结": STATUS_MAINTENANCE_FREEZE,
                    "报废": STATUS_SCRAPPED,
                }[text]
                btn.config(state="normal" if target in allowed else "disabled")
            else:
                btn.config(state="normal" if status == required else "disabled")

    def _get_selected_fixture(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一台灯具")
            return None
        fid = int(sel[0])
        return self.service.db.get_fixture(fid)

    # ---- Filter Handlers ----

    def _on_filter(self):
        loc = self.filter_location.get().strip() or None
        sta = self.filter_status.get().strip() or None
        ds = self.filter_due_start.get().strip() or None
        de = self.filter_due_end.get().strip() or None
        if ds:
            try:
                date.fromisoformat(ds)
            except ValueError:
                messagebox.showerror("格式错误", "到期起始日期格式应为 YYYY-MM-DD")
                return
        if de:
            try:
                date.fromisoformat(de)
            except ValueError:
                messagebox.showerror("格式错误", "到期结束日期格式应为 YYYY-MM-DD")
                return
        self._current_filters = {"location": loc, "status": sta, "due_start": ds, "due_end": de}
        self._persist_filters_to_db()
        self._refresh_table()

    def _on_reset_filter(self):
        self.filter_location.set("")
        self.filter_status.set("")
        self.filter_due_start.delete(0, "end")
        self.filter_due_end.delete(0, "end")
        self._current_filters = {}
        self._persist_filters_to_db()
        self._refresh_table()

    # ---- Selection Handler ----

    def _on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            self.info_label.config(text="请选择灯具")
            self.hist_tree.delete(*self.hist_tree.get_children())
            for btn in self.action_buttons.values():
                btn.config(state="disabled")
            return
        fid = int(sel[0])
        f = self.service.db.get_fixture(fid)
        if not f:
            return
        self._selected_id = fid
        info = (f"编号: {f['fixture_no']}  |  型号: {f['model']}  |  配件: {f['accessories']}\n"
                f"库位: {f['location']}  |  巡检到期: {f['inspection_due_date']}  |  负责人: {f['person_in_charge']}\n"
                f"状态: {f['status']}  |  最近备注: {f['last_remark']}")
        self.info_label.config(text=info)
        self._update_action_buttons(f["status"])
        self._refresh_history(fid)

    def _refresh_history(self, fixture_id):
        self.hist_tree.delete(*self.hist_tree.get_children())
        records = self.service.get_history(fixture_id)
        for r in records:
            change = f"{r['from_status']} → {r['to_status']}" if r['from_status'] else f"→ {r['to_status']}"
            self.hist_tree.insert("", "end", values=(
                r["created_at"], r["action"], change, r["operator"], r["remark"]
            ))

    # ---- Add / Edit / Delete ----

    def _on_add_fixture(self):
        fields = [
            ("fixture_no", "灯具编号", ""),
            ("model", "型号", ""),
            ("accessories", "配件", ""),
            ("location", "库位", ""),
            ("inspection_due_date", "巡检到期日", date.today().isoformat()),
            ("person_in_charge", "负责人", ""),
        ]
        dlg = FormDialog(self, "添加灯具", fields)
        if dlg.result is None:
            return
        r = dlg.result
        if not r["fixture_no"]:
            messagebox.showerror("错误", "灯具编号不能为空")
            return
        if not r["person_in_charge"]:
            messagebox.showerror("错误", "负责人不能为空")
            return
        if r["inspection_due_date"]:
            try:
                date.fromisoformat(r["inspection_due_date"])
            except ValueError:
                messagebox.showerror("错误", "巡检到期日格式应为 YYYY-MM-DD")
                return
        try:
            self.service.add_fixture(r["fixture_no"], r["model"], r["accessories"],
                                     r["location"], r["inspection_due_date"], r["person_in_charge"])
            self._refresh_table()
            messagebox.showinfo("成功", "灯具添加成功")
        except ValueError as e:
            messagebox.showerror("添加失败", str(e))

    def _on_edit_fixture(self):
        f = self._get_selected_fixture()
        if not f:
            return
        fields = [
            ("model", "型号", f["model"]),
            ("accessories", "配件", f["accessories"]),
            ("location", "库位", f["location"]),
            ("inspection_due_date", "巡检到期日", f["inspection_due_date"]),
            ("person_in_charge", "负责人", f["person_in_charge"]),
        ]
        dlg = FormDialog(self, "编辑灯具信息", fields)
        if dlg.result is None:
            return
        r = dlg.result
        if r["inspection_due_date"]:
            try:
                date.fromisoformat(r["inspection_due_date"])
            except ValueError:
                messagebox.showerror("错误", "巡检到期日格式应为 YYYY-MM-DD")
                return
        try:
            self.service.update_fixture_info(f["id"], r["model"], r["accessories"],
                                             r["location"], r["inspection_due_date"], r["person_in_charge"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", "灯具信息已更新")
        except ValueError as e:
            messagebox.showerror("编辑失败", str(e))

    def _on_delete_fixture(self):
        f = self._get_selected_fixture()
        if not f:
            return
        if f["status"] != STATUS_AVAILABLE and f["status"] != STATUS_SCRAPPED:
            messagebox.showerror("删除失败", f"只有「{STATUS_AVAILABLE}」或「{STATUS_SCRAPPED}」状态的灯具可以删除")
            return
        if not messagebox.askyesno("确认删除", f"确定要删除灯具 {f['fixture_no']} 吗？\n相关历史记录也将被删除。"):
            return
        self.service.db.delete_fixture(f["id"])
        self._refresh_table()
        self.info_label.config(text="请选择灯具")
        self.hist_tree.delete(*self.hist_tree.get_children())

    # ---- Status Action Handlers ----

    def _action_dialog(self, title, extra_fields=None, fixture=None):
        fields = [("operator", "操作人", "")]
        if extra_fields:
            fields.extend(extra_fields)
        fields.append(("remark", "备注", ""))
        initial = None
        dlg = FormDialog(self, title, fields, initial)
        if dlg.result is None:
            return None
        if not dlg.result["operator"]:
            messagebox.showerror("错误", "操作人不能为空")
            return None
        return dlg.result

    def _on_borrow(self):
        f = self._get_selected_fixture()
        if not f:
            return
        result = self._action_dialog("借出灯具")
        if result is None:
            return
        try:
            self.service.borrow(f["id"], result["operator"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已借出")
        except ValueError as e:
            messagebox.showerror("借出失败", str(e))

    def _on_return(self):
        f = self._get_selected_fixture()
        if not f:
            return
        result = self._action_dialog("归还登记")
        if result is None:
            return
        try:
            self.service.return_fixture(f["id"], result["operator"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已登记归还，等待复核")
        except ValueError as e:
            messagebox.showerror("归还失败", str(e))

    def _on_review(self):
        f = self._get_selected_fixture()
        if not f:
            return
        result = self._action_dialog("复核入库")
        if result is None:
            return
        try:
            self.service.review_return(f["id"], result["operator"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已复核入库")
        except ValueError as e:
            messagebox.showerror("复核失败", str(e))

    def _on_freeze_inspection(self):
        f = self._get_selected_fixture()
        if not f:
            return
        result = self._action_dialog("巡检冻结")
        if result is None:
            return
        try:
            self.service.freeze_inspection(f["id"], result["operator"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已巡检冻结")
        except ValueError as e:
            messagebox.showerror("冻结失败", str(e))

    def _on_unfreeze_inspection(self):
        f = self._get_selected_fixture()
        if not f:
            return
        extra = [("new_due_date", "新巡检到期日", date.today().isoformat())]
        result = self._action_dialog("巡检解冻", extra_fields=extra)
        if result is None:
            return
        if not result.get("new_due_date"):
            messagebox.showerror("错误", "新巡检到期日不能为空")
            return
        try:
            date.fromisoformat(result["new_due_date"])
        except ValueError:
            messagebox.showerror("错误", "日期格式应为 YYYY-MM-DD")
            return
        try:
            self.service.unfreeze_inspection(f["id"], result["operator"], result["new_due_date"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已巡检解冻")
        except ValueError as e:
            messagebox.showerror("解冻失败", str(e))

    def _on_freeze_maintenance(self):
        f = self._get_selected_fixture()
        if not f:
            return
        result = self._action_dialog("维修冻结")
        if result is None:
            return
        try:
            self.service.freeze_maintenance(f["id"], result["operator"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已维修冻结")
        except ValueError as e:
            messagebox.showerror("冻结失败", str(e))

    def _on_unfreeze_maintenance(self):
        f = self._get_selected_fixture()
        if not f:
            return
        result = self._action_dialog("维修解冻")
        if result is None:
            return
        try:
            self.service.unfreeze_maintenance(f["id"], result["operator"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已维修解冻")
        except ValueError as e:
            messagebox.showerror("解冻失败", str(e))

    def _on_scrap(self):
        f = self._get_selected_fixture()
        if not f:
            return
        result = self._action_dialog("报废灯具")
        if result is None:
            return
        if not messagebox.askyesno("确认报废", f"确定要将灯具 {f['fixture_no']} 标记为报废吗？\n报废后不可恢复。"):
            return
        try:
            self.service.scrap(f["id"], result["operator"], result["remark"])
            self._after_action(f["id"])
            messagebox.showinfo("成功", f"灯具 {f['fixture_no']} 已报废")
        except ValueError as e:
            messagebox.showerror("报废失败", str(e))

    # ---- Batch Import / Rollback GUI ----

    def _on_export_template_csv(self):
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择模板导出目录")
        if not directory:
            return
        filename = "灯具导入模板.csv"
        try:
            path = self.service.export_import_template(directory, filename, fmt="csv")
            self.service.set_export_dir(directory)
            messagebox.showinfo("成功", f"CSV 模板已导出到:\n{path}")
        except (PermissionError, FileNotFoundError) as e:
            messagebox.showerror("导出失败", str(e))

    def _on_export_template_json(self):
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择模板导出目录")
        if not directory:
            return
        filename = "灯具导入模板.json"
        try:
            path = self.service.export_import_template(directory, filename, fmt="json")
            self.service.set_export_dir(directory)
            messagebox.showinfo("成功", f"JSON 模板已导出到:\n{path}")
        except (PermissionError, FileNotFoundError) as e:
            messagebox.showerror("导出失败", str(e))

    def _on_batch_import(self):
        filepath = filedialog.askopenfilename(
            title="选择灯具台账文件",
            filetypes=[("CSV 文件", "*.csv"), ("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not filepath:
            return

        try:
            records = self.service.parse_import_file(filepath)
        except ValueError as e:
            messagebox.showerror("解析失败", str(e))
            return
        except Exception as e:
            messagebox.showerror("解析失败", f"读取文件时出错:\n{e}")
            return

        try:
            precheck_results, summary = self.service.precheck_import(records)
        except Exception as e:
            messagebox.showerror("预检失败", f"预检时出错:\n{e}")
            return

        dlg = PrecheckDialog(self, filepath, precheck_results, summary)
        if not dlg.result:
            return

        action = dlg.result.get("action", "")
        operator = dlg.result.get("operator", "").strip()
        if not operator:
            messagebox.showerror("错误", "操作人不能为空")
            return

        if action == "save_draft":
            remark = dlg.result.get("remark", "").strip()
            try:
                result = self.service.create_draft_from_precheck(
                    filepath, operator, precheck_results, summary,
                    filter_conditions=self._current_filters, remark=remark
                )
                messagebox.showinfo("成功", f"草稿已保存！\n草稿号: {result['draft_no']}\n共 {summary['total']} 条记录\n\n可在「操作 → 导入草稿批次」中继续编辑和提交。")
            except Exception as e:
                messagebox.showerror("保存草稿失败", str(e))
            return

        if action == "import":
            has_precheck_errors = summary[RESULT_ERROR] > 0

            if not has_precheck_errors and not messagebox.askyesno(
                "确认导入",
                f"即将导入 {Path(filepath).name}\n\n"
                f"共 {summary['total']} 条记录:\n"
                f"  新增: {summary[RESULT_NEW]} 条\n"
                f"  更新: {summary[RESULT_UPDATE]} 条\n"
                f"  跳过: {summary[RESULT_SKIP]} 条\n"
                f"  错误: {summary[RESULT_ERROR]} 条\n\n"
                f"操作人: {operator}\n\n"
                f"确认执行导入？",
            ):
                return

            try:
                result = self.service.execute_import(filepath, operator, Path(filepath).name)
                self._refresh_table()
                msg = (f"导入成功！批次号: {result['batch_no']}\n\n"
                       f"共 {result['summary']['total']} 条:\n"
                       f"  新增: {result['summary']['new']} 条\n"
                       f"  更新: {result['summary']['update']} 条\n"
                       f"  跳过: {result['summary']['skip']} 条\n"
                       f"  错误: {result['summary']['error']} 条")
                if result['summary']['error'] > 0:
                    if messagebox.askyesno("导入完成（有错误）", msg + "\n\n是否导出错误清单？"):
                        self._do_export_batch_errors(result['batch_id'])
                else:
                    messagebox.showinfo("导入成功", msg)
            except ValueError as e:
                err_msg = str(e)
                batch_id = getattr(e, 'batch_id', None)
                if batch_id:
                    if messagebox.askyesno("导入失败", err_msg + "\n\n是否立即导出错误清单？"):
                        self._do_export_batch_errors(batch_id)
                else:
                    messagebox.showerror("导入失败", err_msg)
            except Exception as e:
                messagebox.showerror("导入失败", f"导入时发生错误:\n{e}")

    def _on_draft_list(self):
        dlg = DraftListDialog(self, self.service)
        if not dlg.result:
            return

        action = dlg.result.get("action", "")
        draft_id = dlg.result.get("draft_id")

        if action == "new":
            self._on_batch_import()
        elif action == "edit":
            self._open_draft_edit(draft_id)
        elif action == "submit":
            self._open_draft_edit(draft_id)
        elif action == "delete":
            pass
        elif action == "export":
            self._do_export_draft(draft_id)

    def _open_draft_edit(self, draft_id):
        dlg = DraftEditDialog(self, self.service, draft_id)
        if dlg.result and dlg.result.get("action") == "submitted":
            self._refresh_table()
            batch_no = dlg.result.get("batch_no", "")
            summary = dlg.result.get("summary", {})
            msg = (f"提交成功！批次号: {batch_no}\n\n"
                   f"共 {summary.get('total', 0)} 条:\n"
                   f"  新增: {summary.get('new', 0)} 条\n"
                   f"  更新: {summary.get('update', 0)} 条\n"
                   f"  跳过: {summary.get('skip', 0)} 条")
            if messagebox.askyesno("提交成功", msg + "\n\n是否导出本次明细？"):
                self._do_export_batch(dlg.result.get("batch_id"))

    def _do_export_draft(self, draft_id):
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择草稿明细导出目录")
        if not directory:
            return
        draft = self.service.db.get_draft_batch(draft_id)
        if not draft:
            messagebox.showerror("错误", "草稿不存在")
            return
        filename = f"草稿明细_{draft['draft_no']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            path = self.service.export_draft_items(draft_id, directory, filename, selected_only=False)
            self.service.set_export_dir(directory)
            messagebox.showinfo("导出成功", f"草稿明细已导出到:\n{path}")
        except (PermissionError, FileNotFoundError) as e:
            messagebox.showerror("导出失败", str(e))

    def _do_export_batch(self, batch_id):
        batch = self.service.db.get_import_batch(batch_id)
        if not batch:
            messagebox.showerror("错误", "批次不存在")
            return
        detail = self.service.get_import_batch_detail(batch_id)
        if not detail:
            messagebox.showerror("错误", "批次明细不存在")
            return
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择批次明细导出目录")
        if not directory:
            return
        filename = f"批次明细_{batch['batch_no']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            items = detail["items"]
            filepath = Path(directory) / filename
            with open(filepath, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(["行号", "灯具编号", "结果", "信息", "变更详情"])
                for item in items:
                    detail_parts = []
                    if item.get("before_snapshot") and item.get("after_snapshot"):
                        before = item.get("before_obj") or {}
                        after = item.get("after_obj") or {}
                        for field in ["model", "accessories", "location", "inspection_due_date", "person_in_charge", "status"]:
                            old_val = before.get(field, "")
                            new_val = after.get(field, "")
                            if field == "status" and not new_val:
                                continue
                            if new_val and new_val != old_val:
                                detail_parts.append(f"{field}: {old_val} → {new_val}")
                    detail_str = "; ".join(detail_parts)
                    msg = item.get("error_message", "") or (item["result"] == RESULT_NEW and "新增成功") or (item["result"] == RESULT_UPDATE and "更新成功") or (item["result"] == RESULT_SKIP and "跳过（无变化）") or ""
                    writer.writerow([item["row_index"], item["fixture_no"], item["result"], msg, detail_str])
            self.service.set_export_dir(directory)
            messagebox.showinfo("导出成功", f"批次明细已导出到:\n{filepath}")
        except (PermissionError, FileNotFoundError) as e:
            messagebox.showerror("导出失败", str(e))

    def _on_batch_history(self):
        dlg = BatchHistoryDialog(self, self.service)
        if dlg.result and dlg.result.get("action") == "rollback":
            batch_id = dlg.result["batch_id"]
            self._do_rollback(batch_id)
        elif dlg.result and dlg.result.get("action") == "export_errors":
            batch_id = dlg.result["batch_id"]
            self._do_export_batch_errors(batch_id)

    def _do_rollback(self, batch_id):
        fields = [("operator", "操作人", "")]
        dlg = FormDialog(self, "批次回滚", fields)
        if dlg.result is None:
            return
        operator = dlg.result.get("operator", "").strip()
        if not operator:
            messagebox.showerror("错误", "操作人不能为空")
            return

        try:
            result = self.service.rollback_batch(batch_id, operator)
            self._refresh_table()
            msg = (f"回滚完成！批次号: {result['batch_no']}\n\n"
                   f"成功回滚: {result['rolled_back']} 条")
            if result['conflicts']:
                conflict_details = "\n".join(
                    f"  {c['fixture_no']}: {c['reason']}" for c in result['conflicts']
                )
                msg += f"\n\n存在冲突，跳过 {len(result['conflicts'])} 条:\n{conflict_details}"
            messagebox.showinfo("回滚完成", msg)
        except ValueError as e:
            messagebox.showerror("回滚失败", str(e))
        except Exception as e:
            messagebox.showerror("回滚失败", f"回滚时发生错误:\n{e}")

    def _do_export_batch_errors(self, batch_id):
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择错误清单导出目录")
        if not directory:
            return
        batch = self.service.db.get_import_batch(batch_id)
        if not batch:
            messagebox.showerror("错误", "批次不存在")
            return
        filename = f"批次错误_{batch['batch_no']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            path = self.service.export_batch_errors(batch_id, directory, filename)
            self.service.set_export_dir(directory)
            messagebox.showinfo("导出成功", f"错误清单已导出到:\n{path}")
        except (PermissionError, FileNotFoundError) as e:
            messagebox.showerror("导出失败", str(e))

    # ---- Export ----

    def _get_current_filtered_fixtures(self):
        return self.service.get_fixtures(**self._current_filters)

    def _on_export_csv(self):
        fixtures = self._get_current_filtered_fixtures()
        if not fixtures:
            messagebox.showwarning("提示", "当前筛选结果为空，无数据可导出")
            return
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择导出目录")
        if not directory:
            return
        filename = f"灯具导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            path = self.service.export_csv(fixtures, directory, filename)
            self.service.set_export_dir(directory)
            messagebox.showinfo("导出成功", f"CSV 已导出到:\n{path}\n共 {len(fixtures)} 条记录")
        except (PermissionError, FileNotFoundError) as e:
            messagebox.showerror("导出失败", str(e))
        except Exception as e:
            messagebox.showerror("导出失败", f"导出时发生错误:\n{e}")

    def _on_export_json(self):
        fixtures = self._get_current_filtered_fixtures()
        if not fixtures:
            messagebox.showwarning("提示", "当前筛选结果为空，无数据可导出")
            return
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择导出目录")
        if not directory:
            return
        filename = f"灯具导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            path = self.service.export_json(fixtures, directory, filename)
            self.service.set_export_dir(directory)
            messagebox.showinfo("导出成功", f"JSON 已导出到:\n{path}\n共 {len(fixtures)} 条记录")
        except (PermissionError, FileNotFoundError) as e:
            messagebox.showerror("导出失败", str(e))
        except Exception as e:
            messagebox.showerror("导出失败", f"导出时发生错误:\n{e}")

    def _on_set_export_dir(self):
        export_dir = self.service.get_export_dir()
        directory = filedialog.askdirectory(initialdir=export_dir, title="选择常用导出目录")
        if directory:
            if not os.access(directory, os.W_OK):
                messagebox.showerror("错误", f"目录 '{directory}' 不可写")
                return
            self.service.set_export_dir(directory)
            messagebox.showinfo("成功", f"常用导出目录已设置为:\n{directory}")


def main():
    db = DatabaseManager(DB_FILE)
    service = LightingService(db)

    app = Application(service)

    def on_close():
        db.close()
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
