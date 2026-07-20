from scripts.import_canonical_to_postgres import build_parser as import_parser
from scripts.review_knowledge_item import build_parser as review_parser
from src.database.services.payloads import review_action_payload


def test_import_cli_parsing():
    args = import_parser().parse_args(["--knowledge", "k.json", "--manifest", "m.json", "--dry-run"])
    assert args.dry_run is True
    assert args.activate is True


def test_review_cli_parsing():
    args = review_parser().parse_args(["reject", "--item-id", "00000000-0000-0000-0000-000000000001", "--notes", "x", "--yes"])
    assert args.command == "reject"
    assert args.yes is True


def test_history_serializer_keeps_cli_contract():
    class SyntheticAction:
        id = "00000000-0000-0000-0000-000000000001"
        action = "approve"
        previous_status = "pending_review"
        new_status = "approved"
        source = "cli"
        created_at = None
        corrected_payload = {"private": "not exposed"}
        item_content_hash = "not exposed"

    assert review_action_payload(SyntheticAction()) == {
        "id": SyntheticAction.id,
        "action": "approve",
        "previous_status": "pending_review",
        "new_status": "approved",
        "source": "cli",
        "created_at": None,
    }
