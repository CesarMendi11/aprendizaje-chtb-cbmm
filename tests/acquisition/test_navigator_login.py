from __future__ import annotations

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_assistant.acquisition.browser.navigator import ERPNavigator


class _Locator:
    def __init__(self):
        self.filled = []
        self.clicked = 0

    def fill(self, value):
        self.filled.append(value)

    def click(self):
        self.clicked += 1


class _Page:
    def __init__(self, *, final_url: str | None = None, timeout: bool = False):
        self.url = "http://localhost:8080/login"
        self.final_url = final_url
        self.timeout = timeout
        self.username = _Locator()
        self.password = _Locator()
        self.submit = _Locator()
        self.wait_for_url_calls = []
        self.wait_for_timeout_calls = []

    def goto(self, url, wait_until=None):
        self.url = url

    def wait_for_timeout(self, value):
        self.wait_for_timeout_calls.append(value)

    def locator(self, selector):
        if selector == "#username":
            return self.username
        if selector == "#password":
            return self.password
        raise AssertionError(selector)

    def get_by_role(self, role, name=None):
        assert role == "button"
        assert name == "Iniciar sesión"
        return self.submit

    def wait_for_load_state(self, state):
        assert state == "domcontentloaded"

    def wait_for_url(self, predicate, timeout=None):
        self.wait_for_url_calls.append(timeout)
        if self.timeout:
            raise PlaywrightTimeoutError("timed out")
        if self.final_url is not None:
            self.url = self.final_url
        assert predicate(self.url)


def _profile():
    return {
        "erp": {"base_url": "http://localhost:8080"},
        "login": {
            "url": "/login",
            "username_env": "ERP_USERNAME",
            "password_env": "ERP_PASSWORD",
            "username_selector": "#username",
            "password_selector": "#password",
            "submit_role_name": "Iniciar sesión",
            "success_url_contains": "/admin/home",
            "wait_until": "domcontentloaded",
            "initial_wait_ms": 1000,
            "post_login_timeout_ms": 20000,
            "post_login_wait_ms": 3000,
        },
        "navigation": {"home_url": "/admin/home"},
    }


def test_login_waits_for_delayed_spa_navigation(monkeypatch):
    monkeypatch.setenv("ERP_USERNAME", "admin")
    monkeypatch.setenv("ERP_PASSWORD", "secret")
    page = _Page(final_url="http://localhost:8080/admin/home")

    ERPNavigator(page, _profile()).login()

    assert page.wait_for_url_calls == [20000]
    assert page.url.endswith("/admin/home")
    assert page.username.filled == ["admin"]
    assert page.password.filled == ["secret"]
    assert page.submit.clicked == 1
    assert page.wait_for_timeout_calls == [1000, 3000]


def test_login_reports_delayed_spa_navigation_timeout(monkeypatch):
    monkeypatch.setenv("ERP_USERNAME", "admin")
    monkeypatch.setenv("ERP_PASSWORD", "secret")
    page = _Page(timeout=True)

    with pytest.raises(RuntimeError, match="dentro del timeout"):
        ERPNavigator(page, _profile()).login()

    assert page.wait_for_url_calls == [20000]
