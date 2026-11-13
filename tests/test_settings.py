import pytest

from dxvk_updater import settings
from dxvk_updater.game import game_support


def test_remembered_game_precedes_neighbour_and_invalid_saved_folder_falls_back(tmp_path, monkeypatch):
    beside, saved = tmp_path / "beside", tmp_path / "remembered ü"
    for folder in (beside, saved):
        folder.mkdir()
        (folder / "eldorado.exe").write_bytes(b"synthetic")
    monkeypatch.chdir(beside)
    monkeypatch.setattr(settings, "settings_directory", lambda: tmp_path / "settings")
    settings.remember_game(saved)
    assert settings.suggested_game() == saved
    (saved / "eldorado.exe").unlink()
    assert settings.suggested_game() == beside


@pytest.mark.parametrize("data,supported,label", [
    (b"\x000.7.0\x00stuff\x000.7.1\x00", True, "0.7.1"),
    (b"0.7.2-1746131840.debug\x00", False, "debug"),
    (b"unknown", False, "Unrecognized"),
])
def test_game_build_identification(game, data, supported, label):
    (game / "mtndew.dll").write_bytes(data)
    result = game_support(game)
    assert result[1] is supported
    assert label in result[0]
