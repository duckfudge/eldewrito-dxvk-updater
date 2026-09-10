from __future__ import annotations

import os
import sys
from pathlib import Path

from .model import UpdaterError, read_json
from .storage import atomic_json


def settings_directory():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "ElDewritoDXVKUpdater"


def suggested_game():
    beside = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    if (beside / "eldorado.exe").is_file():
        return beside
    try:
        data = read_json((settings_directory() / "settings.json").read_bytes())
        game = Path(data["game_folder"])
        if (game / "eldorado.exe").is_file():
            return game
    except (OSError, UpdaterError, KeyError, TypeError):
        pass
    return None


def remember_game(game):
    directory = settings_directory()
    directory.mkdir(parents=True, exist_ok=True)
    atomic_json(directory / "settings.json", {"game_folder": str(game)})
