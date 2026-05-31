import pytest

from src.auth.auth_manager import AuthManager


def build_profile() -> dict:
    return {
        "erp": {
            "base_url": "http://localhost:8080",
        },
        "login": {
            "url": "/login",
            "username_env": "ERP_USERNAME",
            "password_env": "ERP_PASSWORD",
            "username_selector": "input[type='text']",
            "password_selector": "input[type='password']",
            "submit_role_name": "Iniciar sesión",
            "success_url_contains": "/admin/home",
        },
    }


def test_auth_manager_builds_login_url():
    manager = AuthManager(build_profile())

    assert manager.get_login_url() == "http://localhost:8080/login"


def test_auth_manager_reads_credentials_from_env(monkeypatch):
    monkeypatch.setenv("ERP_USERNAME", "admin")
    monkeypatch.setenv("ERP_PASSWORD", "secret")

    manager = AuthManager(build_profile())
    credentials = manager.get_credentials()

    assert credentials.username == "admin"
    assert credentials.password == "secret"


def test_auth_manager_fails_without_credentials(monkeypatch):
    monkeypatch.delenv("ERP_USERNAME", raising=False)
    monkeypatch.delenv("ERP_PASSWORD", raising=False)

    manager = AuthManager(build_profile())

    with pytest.raises(RuntimeError):
        manager.get_credentials()


def test_auth_manager_validates_successful_login_url():
    manager = AuthManager(build_profile())

    assert manager.is_successful_login_url("http://localhost:8080/admin/home")
    assert not manager.is_successful_login_url("http://localhost:8080/login")