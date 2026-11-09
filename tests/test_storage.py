from types import SimpleNamespace

import psutil
import pytest

from dxvk_updater.model import UpdaterError
from dxvk_updater.storage import ensure_game_closed


@pytest.mark.parametrize("case,blocked", [("matching", True), ("other", False), ("denied", True), ("exited", False), ("unrelated", False)])
def test_real_game_guard_matches_process_path_and_handles_races(game, monkeypatch, case, blocked):
    def executable():
        if case == "denied":
            raise psutil.AccessDenied(123)
        if case == "exited":
            raise psutil.NoSuchProcess(123)
        return str((game.parent if case == "other" else game) / "eldorado.exe")
    process = SimpleNamespace(info={"name": "notepad.exe" if case == "unrelated" else "ELDORADO.EXE"}, exe=executable)
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [process])
    if blocked:
        with pytest.raises(UpdaterError, match="Close|running"):
            ensure_game_closed(game)
    else:
        ensure_game_closed(game)
