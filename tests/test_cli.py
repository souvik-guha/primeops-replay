import json

from helpers import SAMPLE

from primeops_replay.cli import main


def run(*args, data=SAMPLE):
    return main([*args, "--data-dir", str(data)])


def run_json(capsys, *args):
    assert run(*args, "--json") == 0
    return json.loads(capsys.readouterr().out)


def test_readme_commands(capsys):
    assert run("show", "--trade-id", "T1001", "--as-of", "2026-11-18T09:50:00+08:00") == 0
    assert "BREAK_SHORT" in capsys.readouterr().out
    assert run("show", "--trade-id", "T1001", "--as-of", "2026-11-18T09:53:00+08:00") == 0
    out = capsys.readouterr().out
    assert "MATCHED" in out and "duplicate message, ignored" in out


def test_time_without_offset_means_hong_kong(capsys):
    out = run_json(capsys, "show", "--trade-id", "T1001", "--as-of", "2026-11-18T09:50")
    assert out["as_of"] == "2026-11-18T09:50:00+08:00"


def test_unknown_trade_exits_2(capsys):
    assert run("show", "--trade-id", "T9999", "--as-of", "2026-11-18T11:00") == 2
    assert "not in the input files" in capsys.readouterr().err


def test_rejected_revision_falls_back_and_is_shown(capsys):
    out = run_json(capsys, "show", "--trade-id", "T1004", "--as-of", "2026-11-18T11:00")
    assert (out["status"], out["revision_used"]) == ("MATCHED", 1)
    assert any(note.startswith("message M-4002: rejected") for note in out["notes"])


def test_rejection_is_not_shown_before_it_arrived(capsys):
    assert run_json(capsys, "show", "--trade-id", "T1004", "--as-of", "2026-11-18T10:26")["notes"] == []


def test_errors_are_reported_without_a_traceback(tmp_path, capsys):
    assert run("summary", "--as-of", "2026-11-18T11:00", data=tmp_path / "nowhere") == 2
    assert run("summary", "--as-of", "not-a-time") == 2
    assert capsys.readouterr().err.count("error:") == 2
