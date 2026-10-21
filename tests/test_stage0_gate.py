import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("stage0_gate", Path(__file__).resolve().parent.parent / "scripts" / "stage0_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

HEADER = "项目名,私信日期,回复,访谈日期,每月付款对账小时,愿给额度(USDT),愿付价格(USDT/月),额度=是,价格=是,试用=是\n"


def write(tmp_path, rows):
    p = tmp_path / "sheet.csv"
    p.write_text("﻿" + HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return p


def test_empty_sheet_fails_gate(tmp_path, capsys):
    assert gate.main([str(write(tmp_path, []))]) == 1
    out = capsys.readouterr().out
    assert "名单 0" in out and "还差 3 家" in out


def test_funnel_and_medians(tmp_path):
    rows = [
        "A,2026-10-21,是,2026-10-23,10,500,199,是,是,是",
        "B,2026-10-21,是,2026-10-24,6 小时,\"1,000\",99 USDT,是,否,是",
        "C,2026-10-21,否,,,,,,,",
        "D,,,,,,,,,",
        ",,,,,,,,,",
    ]
    s = gate.score(gate.load(write(tmp_path, rows)))
    assert (s["listed"], s["messaged"], s["replied"], s["interviewed"]) == (4, 3, 2, 2)
    assert s["trials"] == ["A", "B"] and s["strong"] == ["A"]
    assert s["median_price"] == 149 and s["median_limit"] == 750 and s["median_hours"] == 8
    assert not s["passed"]


def test_three_written_trials_pass(tmp_path, capsys):
    rows = [f"T{i},2026-10-21,是,2026-10-2{i},,,,yes,yes,✅" for i in range(3)]
    assert gate.main([str(write(tmp_path, rows))]) == 0
    assert "关卡 A：通过" in capsys.readouterr().out
