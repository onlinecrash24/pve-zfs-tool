"""The build's own version, shown on the login page and the home page.

Answers the first question in every bug report before it is asked. It comes from
the git tag via a Docker build arg rather than a file in the repo, so it cannot
drift from the release it claims to be -- there is nothing to remember to bump,
and a build that is not a release says so.
"""

import os
import re

import pytest

from app import main as m


@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["csrf_token"] = "tok"
    return c


# --- the value itself -----------------------------------------------------

def test_a_release_build_reports_its_tag(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.2.3")
    assert m.app_version() == "v1.2.3"


def test_a_build_without_a_version_says_dev(monkeypatch):
    # Honest rather than a placeholder: a source build is not a release.
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert m.app_version() == "dev"


def test_an_empty_value_is_treated_as_no_version(monkeypatch):
    # An unset build arg can arrive as an empty string; "" in the corner of the
    # login page would look like a rendering bug.
    monkeypatch.setenv("APP_VERSION", "   ")
    assert m.app_version() == "dev"


# --- where it shows up ----------------------------------------------------

def test_the_login_page_shows_it(client, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.2.3")
    body = client.get("/login").get_data(as_text=True)
    assert "v1.2.3" in body


def test_the_login_footer_links_to_the_repository(client):
    # The login page is where somebody who did not deploy this lands. A link
    # out is the only way from there to "what is this and who wrote it".
    body = client.get("/login").get_data(as_text=True)
    assert "https://github.com/onlinecrash24/pve-zfs-tool" in body
    assert "MIT License" in body


def test_the_outbound_link_cannot_reach_back(client):
    # target=_blank without noopener hands the opened page a reference to this
    # one -- on a login screen of all places. Checked on the anchor itself, so
    # a noopener sitting on some other element would not satisfy it.
    body = client.get("/login").get_data(as_text=True)
    anchor = re.search(r"<a [^>]*github\.com/onlinecrash24[^>]*>", body).group(0)
    assert 'target="_blank"' in anchor
    assert "noopener" in anchor


def test_the_home_page_gets_it_as_a_global(client, monkeypatch):
    # The home header is built in JavaScript, so the value travels as a global
    # rather than as markup.
    monkeypatch.setenv("APP_VERSION", "v1.2.3")
    body = client.get("/").get_data(as_text=True)
    assert 'window.ZFS_VERSION = "v1.2.3"' in body


def test_both_pages_still_render_without_a_version(client, monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert client.get("/login").status_code == 200
    assert client.get("/").status_code == 200


# --- the build wiring that produces it ------------------------------------

def _repo_file(*parts):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_dockerfile_accepts_and_exports_the_version():
    dockerfile = _repo_file("Dockerfile")
    assert "ARG APP_VERSION" in dockerfile
    assert "ENV APP_VERSION=$APP_VERSION" in dockerfile


def test_every_image_build_passes_the_version():
    # There are two build steps -- the cached one and the no-cache fallback that
    # runs when it fails. A version on only one of them means a fallback build
    # silently ships as "dev" while claiming to be a release.
    workflow = _repo_file(".github", "workflows", "docker-publish.yml")
    builds = workflow.count("build-push-action")
    passes = workflow.count("APP_VERSION=${{ github.ref_name }}")
    assert builds == passes, f"{builds} build steps but {passes} carry the version"
