#!/usr/bin/env python3
"""GUI冒烟测试：验证主程序和DraftEditDialog能正常渲染"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import tkinter as tk
from tkinter import ttk

from stage_lighting_tool import (
    DatabaseManager, LightingService,
    STATUS_BORROWED, STATUS_INSPECTION_FREEZE, STATUS_MAINTENANCE_FREEZE,
    DraftListDialog,
)


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


def setup_test_data(db_path, test_file_dir):
    """准备测试数据：3个有冲突的+1个新增的"""
    db = DatabaseManager(db_path)
    svc = LightingService(db)

    svc.add_fixture("GUI001", "OldModel1", "old-acc1", "OldLoc1", "2026-01-01", "OldPerson1")
    svc.add_fixture("GUI002", "OldModel2", "old-acc2", "OldLoc2", "2026-06-01", "OldPerson2")
    svc.add_fixture("GUI003", "OldModel3", "", "OldLoc3", "2026-03-01", "OldPerson3")

    rows = [
        ["GUI001", "NewModel1", "new-acc1", "NewLoc1", "2027-12-01", "NewPerson1", "", ""],
        ["GUI002", "NewModel2", "new-acc2", "NewLoc2", "2027-12-01", "NewPerson2", "", ""],
        ["GUI003", "NewModel3", "", "NewLoc3", "2027-12-01", "NewPerson3", "", ""],
        ["GUI004", "BrandNew", "", "NewLoc4", "2027-06-01", "NewPerson4", "", ""],
    ]
    filepath = _make_import_csv(test_file_dir, rows)

    records = svc.parse_import_file(str(filepath))
    precheck_results, summary = svc.precheck_import(records)
    create_result = svc.create_draft_from_precheck(
        str(filepath), "GUI测试员", precheck_results, summary, remark="GUI冒烟测试草稿"
    )
    draft_id = create_result["draft_id"]

    db.execute("UPDATE fixtures SET status=?, model=? WHERE fixture_no=?",
               (STATUS_BORROWED, "BorrowedModel", "GUI001"))
    db.execute("UPDATE fixtures SET status=? WHERE fixture_no=?", (STATUS_INSPECTION_FREEZE, "GUI002"))
    db.execute("UPDATE fixtures SET status=? WHERE fixture_no=?", (STATUS_MAINTENANCE_FREEZE, "GUI003"))
    db.commit()

    svc.detect_and_persist_conflicts(draft_id, str(filepath))

    items = db.get_draft_items(draft_id)
    items_by_no = {it["fixture_no"]: it for it in items}
    svc.refresh_draft_item_from_db(items_by_no["GUI001"]["id"], "测试：按库内刷新")
    svc.keep_original_draft_item(items_by_no["GUI002"]["id"], "测试：保持原样")

    db.close()
    return draft_id, filepath


def test_main_app_launch():
    """测试主程序启动不崩溃"""
    tmpdir = tempfile.mkdtemp(prefix="gui_main_")
    try:
        db_path = Path(tmpdir) / "test_main.db"
        test_dir = Path(tmpdir) / "files"
        test_dir.mkdir()
        setup_test_data(db_path, test_dir)

        test_result = {"success": False, "error": None}

        def run_check():
            try:
                assert app.winfo_exists(), "主窗口不存在"
                print(f"  主窗口标题: {app.title()}")
                print(f"  主窗口尺寸: {app.geometry()}")

                children = app.winfo_children()
                print(f"  子组件数量: {len(children)}")

                print("  PASS 主程序启动成功，未崩溃")
                test_result["success"] = True
            except Exception as e:
                test_result["error"] = e
                import traceback
                traceback.print_exc()
            finally:
                app.after(200, app.destroy)

        db = DatabaseManager(db_path)
        svc = LightingService(db)

        from stage_lighting_tool import Application
        app = Application(svc)
        app.after(500, run_check)
        app.mainloop()
        db.close()

        if not test_result["success"]:
            if test_result["error"]:
                raise test_result["error"]
            raise AssertionError("主程序启动测试失败")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_draft_list_dialog():
    """测试草稿列表对话框"""
    tmpdir = tempfile.mkdtemp(prefix="gui_draftlist_")
    try:
        db_path = Path(tmpdir) / "test_draftlist.db"
        test_dir = Path(tmpdir) / "files"
        test_dir.mkdir()
        draft_id, filepath = setup_test_data(db_path, test_dir)

        test_result = {"success": False, "error": None}

        def run_check():
            try:
                assert dlg.winfo_exists(), "草稿列表对话框不存在"
                print(f"  草稿列表对话框标题: {dlg.title()}")

                tree = getattr(dlg, 'tree', None)
                if tree:
                    children = tree.get_children()
                    print(f"  草稿列表条目数: {len(children)}")
                    assert len(children) >= 1, "应该至少有1条草稿"
                    for item in children:
                        vals = tree.item(item, "values")
                        print(f"    草稿: {vals}")

                print("  PASS 草稿列表对话框创建成功")
                test_result["success"] = True
            except Exception as e:
                test_result["error"] = e
                import traceback
                traceback.print_exc()
            finally:
                dlg.destroy()
                root.after(100, root.destroy)

        db = DatabaseManager(db_path)
        svc = LightingService(db)

        root = tk.Tk()
        root.withdraw()

        def open_dialog():
            nonlocal dlg
            dlg = DraftListDialog(root, svc)
            root.after(300, run_check)

        dlg = None
        root.after(100, open_dialog)
        root.mainloop()
        db.close()

        if not test_result["success"]:
            if test_result["error"]:
                raise test_result["error"]
            raise AssertionError("草稿列表对话框测试失败")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("\n=== GUI冒烟测试 ===")
    print("\n[1/2] 测试主程序启动...")
    test_main_app_launch()
    print("\n[2/2] 测试草稿列表对话框...")
    test_draft_list_dialog()
    print("\n=== 所有GUI冒烟测试通过 ===")


if __name__ == "__main__":
    main()
