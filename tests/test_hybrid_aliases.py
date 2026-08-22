from pathlib import Path

from src.hybrid.aliases import semantic_aliases_for
from src.knowledge.canonical.ids import normalize_text, stable_id


def test_semantic_aliases_resolve_profile_from_canonical_erp_id(tmp_path: Path):
    profile = tmp_path / "cbmm.yaml"
    profile.write_text(
        """
erp:
  name: ERP Demo
  code: cbmm
semantic_aliases:
  RUC:
    - identificacion tributaria
""".strip()
        + "\n",
        encoding="utf-8",
    )

    slug = normalize_text("cbmm").replace(" ", "-")
    erp_id = stable_id("erp", slug)

    assert semantic_aliases_for(erp_id, config_dir=tmp_path) == {
        "RUC": ["identificacion tributaria"]
    }


def test_semantic_aliases_do_not_match_unknown_canonical_erp_id(tmp_path: Path):
    profile = tmp_path / "cbmm.yaml"
    profile.write_text(
        """
erp:
  name: ERP Demo
  code: cbmm
semantic_aliases:
  RUC:
    - identificacion tributaria
""".strip()
        + "\n",
        encoding="utf-8",
    )

    assert semantic_aliases_for(
        "erp:000000000000000000000000",
        config_dir=tmp_path,
    ) == {}
