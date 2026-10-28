import json
from content_planner.cli import main


def test_cli_writes_actual_output_and_nonzero_on_incomplete(tmp_path, capsys):
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"request_id": "cli", "theme": "毕业季"}), encoding="utf-8")
    output = tmp_path / "output.json"
    code = main([str(path), "--db", str(tmp_path / "test.sqlite3"), "--output", str(output)])
    assert code == 2
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "needs_input"
    assert "needs_input" in capsys.readouterr().out


def test_cli_bad_fault_is_error(tmp_path, capsys):
    assert main(["missing.json", "--fault", "unknown:1"]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "error"
