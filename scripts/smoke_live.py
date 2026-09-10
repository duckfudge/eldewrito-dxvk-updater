"""Exercise the real public feed in a newly created synthetic game folder only."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dxvk_updater.engine import Installer
from dxvk_updater.model import FILES, sha256_file
from dxvk_updater.network import FeedClient


def main():
    game = ROOT / "build" / "live-smoke" / ("Test game ü 日本語 " + uuid.uuid4().hex)
    game.mkdir(parents=True)
    (game / "eldorado.exe").write_bytes(b"Synthetic test marker. Never execute.")
    (game / "unrelated.txt").write_text("keep me", encoding="utf-8")
    client = FeedClient()
    try:
        manifest = client.fetch_manifest()
        installer = Installer(game, client)
        assert installer.check(manifest).kind == "install"
        installer.install(manifest)
        assert installer.check(manifest).kind == "current"
        for file in manifest.files:
            assert sha256_file(game / file.name) == file.sha256
        cache = game / "eldorado.dxvk-cache"
        original_cache = cache.read_bytes()
        grown_cache = original_cache + b"synthetic gameplay cache growth"
        cache.write_bytes(grown_cache)
        assert installer.check(manifest).kind == "current"
        installer.install(manifest, repair=True)
        assert cache.read_bytes() == original_cache
        installer.restore()
        assert cache.read_bytes() == grown_cache
        assert installer.check(manifest).kind == "current"
        (game / "d3d9.dll").unlink()
        assert installer.check(manifest).kind == "repair"
        installer.install(manifest)
        assert installer.check(manifest).kind == "current"
        assert cache.read_bytes() == grown_cache
        installer.restore()
        assert not (game / "d3d9.dll").exists()
        assert cache.read_bytes() == grown_cache
        assert (game / "unrelated.txt").read_text(encoding="utf-8") == "keep me"
        installer.recover()
        result = {"result": "passed", "patch_id": manifest.patch_id,
                  "source_commit": manifest.source_commit, "files": list(FILES),
                  "checks": ["real download and installation", "hash verification",
                             "local cache growth", "repair", "restore",
                             "missing-file recovery", "original file absence", "unrelated files"],
                  "fixture": str(game)}
        output = ROOT / "build" / "live-smoke-result.json"
        output.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
