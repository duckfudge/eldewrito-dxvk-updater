"""Bounded HTTPS reads. A manifest can never choose the download origin."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

from .model import MANIFEST_URL, Manifest, PatchFile, UpdaterError, validate_payload


class FeedUnavailable(UpdaterError):
    pass


class DownloadCancelled(UpdaterError):
    pass


class FeedClient:
    def __init__(self, session=None):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def close(self):
        self.session.close()

    def _response(self, url):
        try:
            response = self.session.get(url, timeout=(8, 30), stream=True,
                                        allow_redirects=False,
                                        headers={"User-Agent": "ElDewrito-DXVK-Updater/1.0",
                                                 "Cache-Control": "no-cache", "Accept-Encoding": "identity"})
        except Exception as exc:
            raise FeedUnavailable("Could not reach GitHub. Check your connection and try again.") from exc
        if response.status_code != 200:
            response.close()
            if response.status_code == 404:
                raise FeedUnavailable("The patch feed or file is not published yet. Try again later.")
            raise FeedUnavailable(f"GitHub returned HTTP {response.status_code}. Try again later.")
        if urlsplit(response.url).hostname != "raw.githubusercontent.com":
            response.close()
            raise UpdaterError("Unexpected download location.")
        return response

    def fetch_manifest(self):
        response = self._response(MANIFEST_URL)
        try:
            parts, total = [], 0
            for chunk in response.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > 64 * 1024:
                    raise UpdaterError("The update manifest exceeds its size limit.")
                parts.append(chunk)
            return Manifest.parse(b"".join(parts))
        except UpdaterError:
            raise
        except Exception as exc:
            raise FeedUnavailable("The metadata download was interrupted. Try again.") from exc
        finally:
            response.close()

    def download(self, manifest: Manifest, file: PatchFile, target: Path, progress=lambda n: None, cancelled=lambda: False):
        response = self._response(manifest.download_url(file))
        created = False
        try:
            total, digest = 0, hashlib.sha256()
            with target.open("xb") as stream:
                created = True
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if cancelled():
                        raise DownloadCancelled("Download cancelled. Your installed files were not changed.")
                    total += len(chunk)
                    if total > file.size:
                        raise UpdaterError(f"{file.name} is larger than expected.")
                    stream.write(chunk)
                    digest.update(chunk)
                    progress(len(chunk))
                stream.flush()
                os.fsync(stream.fileno())
            if total != file.size or digest.hexdigest() != file.sha256:
                raise UpdaterError(f"Verification failed for {file.name}. Nothing has been installed.")
            validate_payload(file.name, target.read_bytes())
        except Exception as exc:
            if created:
                target.unlink(missing_ok=True)
            if isinstance(exc, UpdaterError):
                raise
            if isinstance(exc, OSError):
                raise UpdaterError(f"Could not finish downloading {file.name}: {exc}") from exc
            raise FeedUnavailable(f"Downloading {file.name} was interrupted. Try again.") from exc
        finally:
            response.close()
