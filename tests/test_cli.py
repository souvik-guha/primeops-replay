import json
import sqlite3

import pytest
from helpers import SAMPLE

from primeops_replay.cli import main


def run(*args, data=SAMPLE):
    return main([*args, "--data-dir", str(data)])


def run_json(capsys, *args):
    assert run(*args, "--json") == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("engine", ["python", "sql"])
def test_readme_commands(capsys, engine):
    assert run("show", "--trade-id", "T1001", "--as-of", "2026-11-18T09:50:00+08:00", "--engine", engine) == 0
    assert "BREAK_SHORT" in capsys.readouterr().out
    assert run("show", "--trade-id", "T1001", "--as-of", "2026-11-18T09:53:00+08:00", "--engine", engine) == 0
    out = capsys.readouterr().out
    assert "MATCHED" in out and "duplicate message, ignored" in out


def test_time_without_offset_means_hong_kong(capsys):
    out = run_json(capsys, "show", "--trade-id", "T1001", "--as-of", "2026-11-18T09:50")
    assert out["as_of"] == "2026-11-18T09:50:00+08:00"


@pytest.mark.parametrize("engine", ["python", "sql"])
def test_unknown_trade_exits_2(capsys, engine):
    assert run("show", "--trade-id", "T9999", "--as-of", "2026-11-18T11:00", "--engine", engine) == 2
    assert "not in the input files" in capsys.readouterr().err


def test_rejected_revision_falls_back_and_is_shown(capsys):
    out = run_json(capsys, "show", "--trade-id", "T1004", "--as-of", "2026-11-18T11:00")
    assert (out["status"], out["revision_used"]) == ("MATCHED", 1)
    assert any(note.startswith("message M-4002: rejected") for note in out["notes"])


def test_rejection_is_not_shown_before_it_arrived(capsys):
    assert run_json(capsys, "show", "--trade-id", "T1004", "--as-of", "2026-11-18T10:26")["notes"] == []


def test_report_lists_breaks_and_rejected_amendments(tmp_path):
    out = tmp_path / "breaks.csv"
    assert run("report", "--as-of", "2026-11-18T11:00", "--out", str(out)) == 0
    header, *rows = out.read_text(encoding="utf-8").splitlines()
    assert header.endswith(",notes")
    assert [row.split(",")[1] for row in rows] == ["T1002", "T1004"]
    assert ",BREAK_OVER,8000,8400,-400," in rows[0]  # numbers stay numbers
    assert "M-4002: rejected" in rows[1]


def test_rejection_flag_clears_once_a_valid_revision_arrives(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "executions.csv").write_text((SAMPLE / "executions.csv").read_text())
    fixed = {
        "message_id": "M-4003",
        "trade_id": "T1004",
        "revision": 3,
        "received_at": "2026-11-18T10:29:00+08:00",
        "allocations": [{"account": "ACC-A", "quantity": 2000}],
    }
    (data / "allocation_events.jsonl").write_text((SAMPLE / "allocation_events.jsonl").read_text() + json.dumps(fixed))
    out = tmp_path / "breaks.csv"
    run("report", "--as-of", "2026-11-18T10:28", "--out", str(out), data=data)
    assert ",T1004," in out.read_text()
    run("report", "--as-of", "2026-11-18T10:30", "--out", str(out), data=data)
    assert ",T1004," not in out.read_text()


def test_save_db_keeps_a_database_and_rebuilds_it(tmp_path):
    db = tmp_path / "primeops.sqlite"
    for _ in range(2):
        assert run("report", "--as-of", "2026-11-18T11:00", "--out", str(tmp_path / "r.csv"), "--save-db", str(db)) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM allocation_messages").fetchone() == (7,)


def test_save_db_refuses_to_overwrite_other_files(tmp_path, capsys):
    precious = tmp_path / "notes.txt"
    precious.write_text("keep me")
    code = run("report", "--as-of", "2026-11-18T11:00", "--out", str(tmp_path / "r.csv"), "--save-db", str(precious))
    assert code == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    assert precious.read_text() == "keep me"


def test_errors_are_reported_without_a_traceback(tmp_path, capsys):
    assert run("report", "--as-of", "2026-11-18T11:00", "--out", str(tmp_path / "missing" / "r.csv")) == 2
    assert run("summary", "--as-of", "2026-11-18T11:00", data=tmp_path / "nowhere") == 2
    assert run("summary", "--as-of", "not-a-time") == 2
    assert capsys.readouterr().err.count("error:") == 3
