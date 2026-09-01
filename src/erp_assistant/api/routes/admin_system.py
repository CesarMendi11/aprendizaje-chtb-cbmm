from __future__ import annotations

from fastapi import APIRouter, Request

from erp_assistant.api.admin_system_service import collect_admin_system_status
from erp_assistant.api.schemas.admin_system import AdminSystemStatusResponse

router = APIRouter(prefix="/system", tags=["admin-system"])


@router.get(
    "/status",
    response_model=AdminSystemStatusResponse,
)
def system_status(request: Request):
    """Devuelve un snapshot independiente de las dependencias del prototipo."""
    return collect_admin_system_status(request.app.state.semantic_review_session_factory)
