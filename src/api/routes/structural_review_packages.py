from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.dependencies import get_admin_read_session
from src.api.routes.semantic_review import AdminSemanticApiError
from src.api.schemas.structural_review_packages import (
    StructuralReviewChangeResponse,
    StructuralReviewPackagesResponse,
    StructuralScreenReviewPackageResponse,
)
from src.database.services.structural_review_package_service import (
    StructuralReviewPackageError,
    StructuralReviewPackageService,
)
from src.database.services.version_diff_service import VersionDiffError

router = APIRouter(prefix="/knowledge-versions", tags=["structural review packages"])
ReadSession = Annotated[Session, Depends(get_admin_read_session)]


@router.get(
    "/{candidate_version_id}/review-packages", response_model=StructuralReviewPackagesResponse
)
def review_packages(
    candidate_version_id: uuid.UUID,
    session: ReadSession,
    changed_only: bool = False,
    module_id: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> StructuralReviewPackagesResponse:
    service = StructuralReviewPackageService(session)
    try:
        full = service.build(candidate_version_id)
        page = service.build(
            candidate_version_id,
            changed_only=changed_only,
            module_id=module_id,
            limit=limit,
            offset=offset,
        )
    except LookupError as exc:
        raise AdminSemanticApiError(
            404,
            "KnowledgeVersionNotFoundError",
            "not_found",
            "Versión de conocimiento no encontrada.",
        ) from exc
    except (VersionDiffError, StructuralReviewPackageError) as exc:
        raise AdminSemanticApiError(
            422, type(exc).__name__, "invalid_structural_review_package", str(exc)
        ) from exc
    total = sum(
        1
        for package in full.packages
        if (not changed_only or package.review_required)
        and (module_id is None or package.module_id == module_id)
    )

    def change(value):
        return StructuralReviewChangeResponse(**value.__dict__)

    def package(value):
        return StructuralScreenReviewPackageResponse(
            **{**value.__dict__, "changes": tuple(change(item) for item in value.changes)}
        )

    return StructuralReviewPackagesResponse(
        **{
            **page.__dict__,
            "unscoped_changes": tuple(change(item) for item in page.unscoped_changes),
            "packages": tuple(package(item) for item in page.packages),
        },
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(page.packages) if offset + len(page.packages) < total else None,
    )
