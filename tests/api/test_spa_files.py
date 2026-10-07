"""Tests for spa_file_response (src/api/app.py): the PWA files are served from
the dist root with explicit MIME types and caching, SPA paths fall back to
index.html, /api never does, and nothing outside the dist dir is reachable
(design/2026-10-07-mobile-operator-pwa-design.md, decision 9)."""
import json

import pytest

from src.api.app import spa_file_response


@pytest.fixture
def web_dir(tmp_path):
    dist = tmp_path / "dist"
    (dist / "icons").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>MaintainIQ</title>", encoding="utf-8")
    (dist / "sw.js").write_text("self.addEventListener('push', () => {})", encoding="utf-8")
    (dist / "manifest.webmanifest").write_text('{"name": "MaintainIQ"}', encoding="utf-8")
    (dist / "icons" / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    return dist


def _body(response) -> bytes:
    # FileResponse streams from disk; read the file it points at.
    if hasattr(response, "path"):
        with open(response.path, "rb") as fh:
            return fh.read()
    return response.body


def test_service_worker(web_dir):
    resp = spa_file_response(web_dir, "sw.js")
    assert resp.media_type == "text/javascript; charset=utf-8"
    assert resp.headers["cache-control"] == "no-cache"
    assert b"addEventListener" in _body(resp)


def test_manifest(web_dir):
    resp = spa_file_response(web_dir, "manifest.webmanifest")
    assert resp.media_type == "application/manifest+json"
    assert resp.headers["cache-control"] == "no-cache"


def test_icon(web_dir):
    resp = spa_file_response(web_dir, "icons/icon-192.png")
    assert resp.media_type == "image/png"
    assert "cache-control" not in resp.headers


@pytest.mark.parametrize("path", ["m/alerts/42", "", "index.html", "machines/m1"])
def test_spa_paths_fall_back_to_index(web_dir, path):
    resp = spa_file_response(web_dir, path)
    assert resp.media_type == "text/html; charset=utf-8"
    assert resp.headers["cache-control"] == "no-cache"
    assert b"MaintainIQ" in _body(resp)


@pytest.mark.parametrize("path", ["../outside.txt", "icons/../../outside.txt", r"..\outside.txt"])
def test_traversal_serves_index_never_the_outside_file(web_dir, path):
    resp = spa_file_response(web_dir, path)
    assert b"secret" not in _body(resp)
    assert resp.media_type == "text/html; charset=utf-8"


@pytest.mark.parametrize("path", ["api/nope", "api", "api/push/whatever"])
def test_unknown_api_paths_are_json_404s(web_dir, path):
    resp = spa_file_response(web_dir, path)
    assert resp.status_code == 404
    assert json.loads(resp.body) == {"detail": "Not Found"}
