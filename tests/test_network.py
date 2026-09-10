import json
from pathlib import Path

import pytest

from dxvk_updater.model import MANIFEST_URL, UpdaterError
from dxvk_updater.network import DownloadCancelled, FeedClient, FeedUnavailable


class Response:
    def __init__(self, data, status=200, fail=False):
        self.data, self.status_code, self.fail = data, status, fail
        self.url = MANIFEST_URL
        self.closed = False

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size):
        yield self.data[:8]
        if self.fail:
            raise OSError("socket interrupted")
        yield self.data[8:]


class Session:
    def __init__(self, response):
        self.response = response
        self.kwargs = None
        self.url = None

    def get(self, url, **kwargs):
        self.url, self.kwargs = url, kwargs
        return self.response


def test_fetches_bounded_manifest(manifest):
    response = Response(json.dumps(manifest.to_dict()).encode())
    session = Session(response)
    assert FeedClient(session).fetch_manifest() == manifest
    assert session.kwargs["allow_redirects"] is False
    assert session.kwargs["timeout"] == (8, 30)
    assert response.closed


@pytest.mark.parametrize("status", [301, 403, 404, 429, 500])
def test_http_failures(status):
    response = Response(b"error", status)
    with pytest.raises(FeedUnavailable):
        FeedClient(Session(response)).fetch_manifest()
    assert response.closed


def test_oversized_manifest():
    with pytest.raises(UpdaterError, match="size limit"):
        FeedClient(Session(Response(b"x" * 70000))).fetch_manifest()


def test_download_verifies_and_uses_commit(manifest, payloads, tmp_path):
    response = Response(payloads["d3d9.dll"])
    session = Session(response)
    target = tmp_path / "d3d9.dll"
    FeedClient(session).download(manifest, manifest.files[0], target)
    assert target.read_bytes() == payloads["d3d9.dll"]
    assert f"/{manifest.source_commit}/" in session.url


@pytest.mark.parametrize("mode", ["checksum", "truncated", "oversize", "interrupted", "cancelled"])
def test_failed_download_discards_partial(manifest, payloads, tmp_path, mode):
    data = payloads["d3d9.dll"]
    if mode == "checksum":
        data = data[:-1] + b"x"
    elif mode == "truncated":
        data = data[:-1]
    elif mode == "oversize":
        data += b"extra"
    response = Response(data, fail=mode == "interrupted")
    target = tmp_path / "d3d9.dll"
    with pytest.raises(UpdaterError):
        FeedClient(Session(response)).download(manifest, manifest.files[0], target, cancelled=lambda: mode == "cancelled")
    assert not target.exists()
    assert response.closed
