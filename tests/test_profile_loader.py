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