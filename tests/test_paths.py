from handsight import paths


def test_data_defaults_to_project_folder(monkeypatch):
    monkeypatch.delenv(paths.DATA_ENV, raising=False)
    assert paths.data_dir() == paths.ROOT


def test_data_uses_env_folder_and_creates_it(monkeypatch, tmp_path):
    target = tmp_path / "Application Support" / "Handsight"
    monkeypatch.setenv(paths.DATA_ENV, str(target))
    assert paths.data_dir() == target
    assert target.is_dir()


def test_blank_env_means_project_folder(monkeypatch):
    monkeypatch.setenv(paths.DATA_ENV, "   ")
    assert paths.data_dir() == paths.ROOT
