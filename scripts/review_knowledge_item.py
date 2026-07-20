from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.database.repositories import KnowledgeRepository
from src.database.services import EffectiveKnowledgeService, KnowledgeReviewService
from src.database.services.payloads import review_action_payload
from src.database.session import session_scope

from .database_common import database_engine, print_json

MAX_FILE_SIZE = 256_000


def build_parser():
    parser = argparse.ArgumentParser(description="Revisión humana de conocimiento")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "show", "approve", "reject", "correct", "reset", "history"):
        command = sub.add_parser(name)
        command.add_argument("--item-id")
        command.add_argument("--canonical-id")
        command.add_argument("--entity-type")
        command.add_argument("--erp-id")
        if name in {"approve", "reject", "correct", "reset"}:
            command.add_argument("--reviewer")
            command.add_argument("--notes", required=name in {"reject", "correct"})
            command.add_argument("--yes", action="store_true")
            command.add_argument("--expected-revision", type=int)
        if name == "correct":
            command.add_argument("--correction-file", required=True)
    listing = sub.choices["list"]
    listing.add_argument("--status")
    listing.add_argument("--route")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--offset", type=int, default=0)
    return parser


def _item(session, args):
    repo = KnowledgeRepository(session)
    if args.item_id:
        item = repo.get_item(args.item_id)
    else:
        if not args.canonical_id or not args.entity_type:
            raise ValueError("Use --item-id o --canonical-id junto con --entity-type")
        erp_id = args.erp_id
        if not erp_id:
            versions = repo.list_versions()
            active = [v for v in versions if str(v.status) == "active"]
            if len(active) != 1:
                raise ValueError("Indique --erp-id cuando no exista una única versión activa")
            version = active[0]
        else:
            version = repo.get_active_version(erp_id)
        if not version:
            raise LookupError("No existe versión activa")
        item = repo.get_item_by_identity(version.id, args.entity_type, args.canonical_id)
    if not item:
        raise LookupError("KnowledgeItem no encontrado")
    return item


def _summary(item):
    return {
        "id": str(item.id),
        "canonical_id": item.canonical_id,
        "entity_type": item.entity_type,
        "status": str(item.current_review_status),
        "review_revision": item.review_revision,
        "route": item.route,
        "title": item.title,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        with session_scope(database_engine()) as session:
            service = KnowledgeReviewService(session)
            if args.command == "list":
                print_json([_summary(x) for x in service.list_items(
                    status=args.status, entity_type=args.entity_type, route=args.route,
                    limit=args.limit, offset=args.offset
                )], pretty=True)
                return 0
            item = _item(session, args)
            if args.command == "show":
                print_json({
                    **_summary(item),
                    **EffectiveKnowledgeService(session).describe(item.id),
                }, pretty=True)
                return 0
            if args.command == "history":
                print_json([
                    review_action_payload(action)
                    for action in service.get_review_history(item.id)
                ], pretty=True)
                return 0
            print_json({"proposed_action": args.command, "item": _summary(item)}, pretty=True)
            if not args.yes and input("¿Confirmar? [y/N] ").strip().casefold() not in {"y", "yes", "s", "si", "sí"}:
                print("Cancelado")
                return 1
            kwargs = dict(
                reviewer=args.reviewer, notes=args.notes,
                expected_revision=args.expected_revision
            )
            if args.command == "approve":
                changed = service.approve(item.id, **kwargs)
            elif args.command == "reject":
                changed = service.reject(item.id, **kwargs)
            elif args.command == "reset":
                changed = service.reset_to_pending(item.id, **kwargs)
            else:
                path = Path(args.correction_file).expanduser()
                if not path.is_file() or path.stat().st_size > MAX_FILE_SIZE:
                    raise ValueError("Archivo de corrección inválido o demasiado grande")
                payload = json.loads(path.read_text(encoding="utf-8"))
                changed = service.correct(item.id, payload, **kwargs)
            print_json({"status": "ok", "item": _summary(changed)}, pretty=True)
        return 0
    except (OSError, ValueError, LookupError, json.JSONDecodeError) as exc:
        print_json({"status": "error", "error": str(exc)[:500]}, pretty=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
