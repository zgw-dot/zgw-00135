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
            CREATE INDEX IF NOT EXISTS idx_fixtures_status ON fixtures(status);
            CREATE INDEX IF NOT EXISTS idx_fixtures_location ON fixtures(location);
            CREATE INDEX IF NOT EXISTS idx_fixtures_inspection ON fixtures(inspection_due_date);
            CREATE INDEX IF NOT EXISTS idx_history_fid ON history(fixture_id);
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
        self._refresh_table()
        self._update_status_bar()

    # ---- Menu ----

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="导出 CSV (当前筛选)...", command=self._on_export_csv)
        file_menu.add_command(label="导出 JSON (当前筛选)...", command=self._on_export_json)
        file_menu.add_separator()
        file_menu.add_command(label="设置常用导出目录...", command=self._on_set_export_dir)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)

        action_menu = tk.Menu(menubar, tearoff=0)
        action_menu.add_command(label="添加灯具", command=self._on_add_fixture)
        action_menu.add_command(label="编辑灯具信息", command=self._on_edit_fixture)
        action_menu.add_command(label="删除灯具", command=self._on_delete_fixture)
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
        self._refresh_table()

    def _on_reset_filter(self):
        self.filter_location.set("")
        self.filter_status.set("")
        self.filter_due_start.delete(0, "end")
        self.filter_due_end.delete(0, "end")
        self._current_filters = {}
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
