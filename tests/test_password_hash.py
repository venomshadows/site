import getpass
from pathlib import Path
import runpy

import pytest
from werkzeug.security import check_password_hash


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "gen_password_hash.py"


@pytest.mark.parametrize("answers, message", [
    ([""], "Пустой пароль не годится."),
    (["secret", "different"], "Пароли не совпадают, попробуй ещё раз."),
])
def test_invalid_password_exits_with_error(monkeypatch, capsys, answers, message):
    answers = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(answers))
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == message


def test_matching_password_prints_valid_hash(monkeypatch, capsys):
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "test-password")
    runpy.run_path(str(SCRIPT), run_name="__main__")
    output = capsys.readouterr()
    assert output.err == ""
    assert check_password_hash(output.out.strip(), "test-password")
