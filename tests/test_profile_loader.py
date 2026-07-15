from pathlib import Path

from src.config.profile_loader import ProfileLoader


def test_profile_loader_loads_cbmm_profile():
    profile_path = Path("configs/cbmm.yaml")

    profile = ProfileLoader(profile_path).load()

    assert profile["erp"]["code"] == "cbmm"
    assert "base_url" in profile["erp"]
    assert "login" in profile
    assert "navigation" in profile
    assert "exploration" in profile
    assert "output" in profile

def test_profile_loader_rejects_unknown_event_category(tmp_path):
    profile_path = tmp_path / "invalid.yaml"
    profile_path.write_text(
        """
erp:
  name: Test
  code: test
  base_url: http://localhost:8080
login:
  url: /login
  username_selector: '#user'
  password_selector: '#password'
  submit_role_name: Ingresar
  success_url_contains: /admin/home
navigation:
  home_url: /admin/home
exploration:
  allowed_routes: [/admin/]
  blocked_routes: []
safety:
  allowed_event_categories: [categoria_inexistente]
extraction: {}
output:
  raw_playwright_dir: data/raw/playwright
  html_dir: data/raw/html
  screenshots_dir: data/raw/screenshots
  processed_structural_dir: data/processed/structural
  review_structural_dir: data/review/structural
""",
        encoding="utf-8",
    )

    try:
        ProfileLoader(profile_path).load()
    except ValueError as error:
        assert "categorías desconocidas" in str(error)
    else:
        raise AssertionError("El perfil inválido debía ser rechazado.")


def test_profile_loader_rejects_invalid_state_replay_value(tmp_path):
    profile_path = tmp_path / "invalid_state_replay.yaml"
    profile_path.write_text(
        """
erp:
  name: Test
  code: test
  base_url: http://localhost:8080
login:
  url: /login
  username_selector: '#user'
  password_selector: '#password'
  submit_role_name: Ingresar
  success_url_contains: /admin/home
navigation:
  home_url: /admin/home
exploration:
  allowed_routes: [/admin/]
  blocked_routes: []
safety: {}
state_replay:
  restore_attempts: -1
extraction: {}
output:
  raw_playwright_dir: data/raw/playwright
  html_dir: data/raw/html
  screenshots_dir: data/raw/screenshots
  processed_structural_dir: data/processed/structural
  review_structural_dir: data/review/structural
""",
        encoding="utf-8",
    )

    try:
        ProfileLoader(profile_path).load()
    except ValueError as error:
        assert "state_replay.restore_attempts" in str(error)
    else:
        raise AssertionError("El perfil inválido debía ser rechazado.")
