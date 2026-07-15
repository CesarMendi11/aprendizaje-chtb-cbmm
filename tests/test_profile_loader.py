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


def test_profile_loader_accepts_exploration_budget():
    profile = ProfileLoader(Path("configs/cbmm.yaml")).load()

    budget = profile["ui_events"]["exploration_budget"]

    assert budget["exclude_global_navigation_outside_home"] is True
    assert budget["category_limits"]["open_dropdown"] == 2
    assert budget["home_category_limits"]["expand_menu"] == 10


def test_profile_loader_rejects_negative_exploration_budget(tmp_path):
    profile_path = tmp_path / "invalid_budget.yaml"
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
ui_events:
  exploration_budget:
    category_limits:
      open_dropdown: -1
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
        assert "enteros no negativos" in str(error)
    else:
        raise AssertionError("El presupuesto inválido debía ser rechazado.")


def test_profile_loader_accepts_state_exploration_scope():
    profile = ProfileLoader(Path("configs/cbmm.yaml")).load()
    ui_events = profile["ui_events"]

    assert ui_events["max_event_depth"] == 1
    assert ui_events["home_navigation_enabled"] is True
    assert ui_events["explore_local_route_roots"] is True
    assert "open_dropdown" in ui_events["local_event_categories"]


def test_profile_loader_rejects_unknown_local_event_category(tmp_path):
    profile_path = tmp_path / "invalid_local_categories.yaml"
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
ui_events:
  max_event_depth: 1
  local_event_categories: [categoria_inexistente]
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
        raise AssertionError("La categoría local inválida debía rechazarse.")


def test_profile_loader_rejects_negative_max_event_depth(tmp_path):
    profile_path = tmp_path / "invalid_event_depth.yaml"
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
ui_events:
  max_event_depth: -1
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
        assert "max_event_depth" in str(error)
    else:
        raise AssertionError("La profundidad negativa debía rechazarse.")


def test_profile_loader_accepts_state_stability_configuration():
    profile = ProfileLoader(Path("configs/cbmm.yaml")).load()
    detection = profile["state_detection"]

    assert detection["navigation_state_routes"] == ["/admin/home"]
    assert detection["stability"]["enabled"] is True
    assert detection["stability"]["required_consecutive_samples"] == 2
    assert detection["stability"]["minimum_observation_ms"] == 500


def test_profile_loader_rejects_zero_required_stability_samples(tmp_path):
    profile_path = tmp_path / "invalid_stability.yaml"
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
state_detection:
  stability:
    enabled: true
    required_consecutive_samples: 0
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
        assert "required_consecutive_samples" in str(error)
    else:
        raise AssertionError("La estabilidad con cero muestras debía rechazarse.")
