import pytest

from crawler.env import TargetPageMissingError, load_env_file, target_page


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_env_file_parses_keys_quotes_comments_and_spacing(tmp_path):
    path = write_env(tmp_path, '# comment\n\ntarget_page = "https://www.facebook.com/UITconfess"\nOTHER=1\n')

    assert load_env_file(path) == {"TARGET_PAGE": "https://www.facebook.com/UITconfess", "OTHER": "1"}


def test_missing_env_file_gives_no_values(tmp_path):
    assert load_env_file(tmp_path / "absent.env") == {}


def test_target_page_comes_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TARGET_PAGE", raising=False)
    path = write_env(tmp_path, "TARGET_PAGE=https://www.facebook.com/UITconfess\n")

    assert target_page(None, env_file=path) == "https://www.facebook.com/UITconfess"


def test_command_line_beats_environment_which_beats_env_file(tmp_path, monkeypatch):
    path = write_env(tmp_path, "TARGET_PAGE=from-file\n")
    monkeypatch.setenv("TARGET_PAGE", "from-environment")

    assert target_page("from-cli", env_file=path) == "from-cli"
    assert target_page(None, env_file=path) == "from-environment"


def test_missing_target_page_explains_how_to_set_it(tmp_path, monkeypatch):
    monkeypatch.delenv("TARGET_PAGE", raising=False)

    with pytest.raises(TargetPageMissingError, match="TARGET_PAGE"):
        target_page(None, env_file=tmp_path / "absent.env")
