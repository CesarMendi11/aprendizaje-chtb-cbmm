from types import SimpleNamespace

from erp_assistant.semantic.services.semantic_retrieval_authorization_service import (
    SemanticRetrievalAuthorizationService,
)
from erp_assistant.structural.canonical.enums import ReviewStatus


class ProposalRepo:
    def __init__(self, proposal):
        self.proposal = proposal

    def get_by_semantic_id(self, semantic_id):
        return self.proposal if semantic_id == self.proposal.semantic_id else None


class Effective:
    def __init__(self, payload):
        self.payload = payload

    def publishable_payload(self, proposal_id):
        return self.payload


class EvidenceBuilder:
    def __init__(self, package):
        self.package = package

    def build(self, version_id, screen_id):
        return self.package


def make_case(*, status=ReviewStatus.APPROVED, revision=1, evidence_hash="e" * 64):
    version = SimpleNamespace(id="version-1", erp_id="erp:test", knowledge_version="v1")
    screen = SimpleNamespace(
        id="screen-db-id",
        canonical_id="screen:retenciones",
        knowledge_version_id=version.id,
        current_review_status=ReviewStatus.APPROVED,
        route="/admin/cuentasxcobrar/retenciones",
    )
    proposal = SimpleNamespace(
        id="proposal-db-id",
        semantic_id="semantic:retenciones-purpose",
        semantic_type="screen_purpose",
        knowledge_version_id=version.id,
        current_review_status=status,
        review_revision=revision,
        evidence_hash=evidence_hash,
        evidence_ids=["evidence:screen"],
        screen_knowledge_item=screen,
    )
    package = SimpleNamespace(
        evidence_hash=evidence_hash,
        primary_evidence_ids=["evidence:screen"],
        evidence_ids=["evidence:screen"],
        fields=[],
        controls=[SimpleNamespace()],
        tables=[],
        ui_states=[],
        events=[],
        transitions=[],
        screen_title="Retenciones",
        screen_route="/admin/cuentasxcobrar/retenciones",
    )
    hit = {
        "semantic_id": proposal.semantic_id,
        "semantic_type": "screen_purpose",
        "canonical_id": screen.canonical_id,
        "screen_id": screen.canonical_id,
        "review_status": str(status),
        "review_revision": revision,
        "evidence_hash": evidence_hash,
        "score": 0.92,
        "distance": 0.08,
    }
    payload = {
        "semantic_type": "screen_purpose",
        "screen_id": screen.canonical_id,
        "purpose_summary": "Permite buscar y consultar retenciones.",
        "supported_capabilities": [
            {
                "statement": "Permite buscar mediante los criterios disponibles.",
                "evidence_refs": ["field:ruc"],
            }
        ],
        "limitations": [],
        "uncertainties": [],
    }
    service = SemanticRetrievalAuthorizationService(
        None,
        proposals=ProposalRepo(proposal),
        effective=Effective(payload),
        evidence_builder=EvidenceBuilder(package),
    )
    return service, version, proposal, package, hit


def test_authorizes_only_current_postgresql_semantic_projection():
    service, version, _proposal, _package, hit = make_case()

    rows = service.authorize_hits([hit], version=version)

    assert len(rows) == 1
    row = rows[0]
    assert row["semantic_id"] == "semantic:retenciones-purpose"
    assert row["screen_id"] == "screen:retenciones"
    assert row["purpose_summary"] == "Permite buscar y consultar retenciones."
    assert row["supported_capabilities"] == [
        "Permite buscar mediante los criterios disponibles."
    ]
    assert row["review_revision"] == 1
    assert row["score"] == 0.92


def test_rejects_projection_when_postgresql_status_or_revision_changed():
    service, version, proposal, _package, hit = make_case()
    proposal.current_review_status = ReviewStatus.REJECTED
    assert service.authorize_hits([hit], version=version) == []

    service, version, proposal, _package, hit = make_case()
    proposal.review_revision = 2
    assert service.authorize_hits([hit], version=version) == []


def test_rejects_projection_when_current_evidence_is_stale():
    service, version, _proposal, package, hit = make_case()
    package.evidence_hash = "f" * 64

    assert service.authorize_hits([hit], version=version) == []


def test_rejects_projection_when_current_structure_is_ineligible():
    service, version, _proposal, package, hit = make_case()
    package.primary_evidence_ids = []

    assert service.authorize_hits([hit], version=version) == []
