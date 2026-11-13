from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote

from dxvk_updater.model import REPOSITORY, UpdaterError


class APIError(UpdaterError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, token=None):
        self.token = token or os.environ.get("PUBLISH_TOKEN")
        if not self.token:
            raise UpdaterError("Set the private Actions secret PUBLISH_TOKEN before publishing.")

    def call(self, method, path, payload=None, *, binary=None, content_type="application/json"):
        if not path.startswith("/") or ".." in path:
            raise ValueError("Expected an API path")
        body = binary if binary is not None else json.dumps(payload).encode() if payload is not None else None
        host = "https://uploads.github.com" if binary is not None else "https://api.github.com"
        request = urllib.request.Request(host + path, data=body, method=method,
                                         headers={"Authorization": f"Bearer {self.token}",
                                                  "User-Agent": "ElDewrito-DXVK-Publisher",
                                                  "Accept": "application/vnd.github+json",
                                                  "X-GitHub-Api-Version": "2022-11-28",
                                                  "Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=120 if binary else 30) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            # Never include headers or raw responses in CI logs.
            raise APIError(exc.code, f"GitHub API {method} request failed (HTTP {exc.code}). Check repository permissions and branch rules.") from None

    def source_head(self):
        return self.call("GET", f"/repos/{REPOSITORY}/commits/main")["sha"]

    def feed_head(self):
        try:
            return self.call("GET", f"/repos/{REPOSITORY}/git/ref/heads/updates")["object"]["sha"]
        except APIError as exc:
            if exc.status == 404:
                return None
            raise

    def read_manifest(self, head):
        return self.read_feed_file("manifest.json", head)

    def read_feed_file(self, name, head):
        if name not in {"manifest.json", "updater.json"}:
            raise ValueError("Unsupported feed file")
        if head is None:
            return None
        try:
            result = self.call("GET", f"/repos/{REPOSITORY}/contents/{name}?ref={head}")
        except APIError as exc:
            if exc.status == 404:
                return None
            raise
        if result.get("encoding") != "base64":
            raise UpdaterError("The existing manifest has an unexpected encoding.")
        return base64.b64decode(result["content"])

    def write_manifest(self, manifest, parent):
        return self.write_feed({"manifest.json": manifest.to_dict()}, parent, f"Publish DXVK patch {manifest.patch_id[:12]}")

    def write_feed(self, files, parent, message):
        if not files or set(files) - {"manifest.json", "updater.json"}:
            raise ValueError("Unsupported feed file")
        entries = []
        for name, data in files.items():
            text = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
            blob = self.call("POST", f"/repos/{REPOSITORY}/git/blobs", {"content": text, "encoding": "utf-8"})
            entries.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        payload = {"tree": entries}
        if parent:
            payload["base_tree"] = self.call("GET", f"/repos/{REPOSITORY}/git/commits/{parent}")["tree"]["sha"]
        tree = self.call("POST", f"/repos/{REPOSITORY}/git/trees", payload)
        commit = self.call("POST", f"/repos/{REPOSITORY}/git/commits", {
            "message": message, "tree": tree["sha"],
            "parents": [parent] if parent else []})
        if parent:
            # Non-fast-forward updates fail, preserving a concurrently published feed.
            self.call("PATCH", f"/repos/{REPOSITORY}/git/refs/heads/updates", {"sha": commit["sha"], "force": False})
        else:
            self.call("POST", f"/repos/{REPOSITORY}/git/refs", {"ref": "refs/heads/updates", "sha": commit["sha"]})
        return commit["sha"]

    def upload_asset(self, release, path):
        return self.call("POST", f"/repos/{REPOSITORY}/releases/{release['id']}/assets?name={quote(path.name)}",
                         binary=path.read_bytes(), content_type="application/zip" if path.suffix == ".zip" else "text/plain")
