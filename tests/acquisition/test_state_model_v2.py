"""Contrato de State Model v2.

Estas pruebas definen qué significa que el modelo de estados esté correcto.
Fallan contra el modelo v1 y deben pasar tras la corrección.

Invariante central:

    La identidad estructural de un UIState no puede depender de información
    que la frontera de privacidad elimina antes de persistir.

Consecuencia observable: dos páginas de una misma tabla son el MISMO estado
funcional con contenido distinto, no dos estados estructurales.
"""

from erp_assistant.acquisition.crawling.state_signature import StateSignatureBuilder
from erp_assistant.acquisition.crawling.ui_event_explorer import (
    EventEffect,
    classify_event_effect,
)
from erp_assistant.structural.canonical.models import Transition as CanonicalTransition
from erp_assistant.structural.canonical.privacy import sanitize_artifact_payload


def _screen(rows_text: str, *, extra_interactives=None) -> dict:
    """Pantalla de tabla paginada; solo cambia el texto de las filas."""
    return {
        "path": "/admin/tramites/actividades",
        "title": "Gestion de Tareas",
        "functional_title": "Gestion de Tareas",
        "visible_text": f"Gestion de Tareas {rows_text}",
        "main_visible_text": f"Gestion de Tareas {rows_text}",
        "regions": {
            "main_content": {
                "visible_text": f"Gestion de Tareas {rows_text}",
                "elements_count": 12,
            },
            "dialog": {"visible_text": "", "elements_count": 0},
        },
        "links": [],
        "buttons": [
            {
                "text": "Siguiente página",
                "type": "button",
                "role": None,
                "tag": "button",
                "region": "main_content",
            }
        ],
        "inputs": [
            {
                "name": "filtro",
                "id": "mat-input-0",
                "type": "text",
                "placeholder": "Buscar",
                "label": "Buscar",
                "tag": "input",
                "region": "main_content",
            }
        ],
        "tables": [
            {
                "headers": ["Código", "Actividad", "Estado"],
                "rows_count": 10,
                "region": "main_content",
            }
        ],
        "custom_interactives": list(extra_interactives or []),
        "dialogs": [],
    }


PAGE_ONE = _screen("ACT-001 Inspeccion Pendiente ACT-002 Permiso Aprobado")
PAGE_TWO = _screen("ACT-011 Certificado Cerrado ACT-012 Rastreo Pendiente")


# --------------------------------------------------------------------------
# 1. Invariante de re-hash: la preimagen debe sobrevivir a la privacidad
# --------------------------------------------------------------------------


def test_structural_summary_survives_the_privacy_boundary():
    """El summary persistido debe conservar todo lo que define la identidad.

    Falla en v1: `visible_text` entra en el fingerprint estructural pero está
    en PERSISTED_DROP_KEYS, así que la preimagen no es reconstruible desde los
    artefactos y la identidad no es explicable ni auditable.
    """
    builder = StateSignatureBuilder()
    signature = builder.build(PAGE_ONE)

    persisted = sanitize_artifact_payload(signature.summary)

    assert persisted == signature.summary, (
        "El summary estructural pierde campos al persistirse: "
        f"faltan {sorted(set(signature.summary) - set(persisted))}"
    )


def test_structural_fingerprint_is_recomputable_from_persisted_summary():
    """Re-hashear el summary persistido debe dar el mismo fingerprint."""
    builder = StateSignatureBuilder()
    signature = builder.build(PAGE_ONE)

    persisted = sanitize_artifact_payload(signature.summary)
    recomputed = builder._hash(persisted)

    assert recomputed == signature.structural_fingerprint


# --------------------------------------------------------------------------
# 2. Paginación: mismo estado estructural, contenido distinto
# --------------------------------------------------------------------------


def test_pagination_does_not_create_a_new_structural_state():
    """Página 1 y página 2 son el mismo estado funcional.

    Misma ruta, mismo título, mismos controles, mismos headers, mismo
    paginador. Lo único que cambia son los registros.
    """
    builder = StateSignatureBuilder()

    first = builder.build(PAGE_ONE)
    second = builder.build(PAGE_TWO)

    assert first.structural_fingerprint == second.structural_fingerprint


def test_pagination_is_still_observable_as_a_content_change():
    """Guardia contra la corrección ingenua.

    Quitar `visible_text` sin más haría que la paginación fuese indetectable
    (`changed=False`) y el crawler perdería el evento. La firma exacta debe
    seguir distinguiendo el cambio de contenido.
    """
    builder = StateSignatureBuilder()

    first = builder.build(PAGE_ONE)
    second = builder.build(PAGE_TWO)

    assert first.exact_fingerprint != second.exact_fingerprint


# --------------------------------------------------------------------------
# 3. No sub-discriminar: los cambios estructurales reales siguen contando
# --------------------------------------------------------------------------


def test_opening_a_dropdown_is_a_structural_change():
    """Abrir un mat-select añade opciones observables: eso sí es otro estado."""
    builder = StateSignatureBuilder()

    closed = _screen(
        "ACT-001 Inspeccion",
        extra_interactives=[
            {
                "text": "--Seleccione--",
                "tag": "mat-select",
                "role": "combobox",
                "aria_expanded": "false",
                "region": "main_content",
            }
        ],
    )
    opened = _screen(
        "ACT-001 Inspeccion",
        extra_interactives=[
            {
                "text": "--Seleccione--",
                "tag": "mat-select",
                "role": "combobox",
                "aria_expanded": "true",
                "region": "main_content",
            },
            *[
                {
                    "text": label,
                    "tag": "mat-option",
                    "role": "option",
                    "aria_selected": "false",
                    "region": "main_content",
                }
                for label in ("Pendiente", "Aprobado", "Cerrado")
            ],
        ],
    )

    assert (
        builder.build(closed).structural_fingerprint
        != builder.build(opened).structural_fingerprint
    )


def test_a_new_control_is_a_structural_change():
    """Un control nuevo en la pantalla cambia la estructura funcional."""
    builder = StateSignatureBuilder()

    before = builder.build(PAGE_ONE)

    with_export = _screen("ACT-001 Inspeccion Pendiente ACT-002 Permiso Aprobado")
    with_export["buttons"].append(
        {
            "text": "Exportar",
            "type": "button",
            "role": None,
            "tag": "button",
            "region": "main_content",
        }
    )

    assert before.structural_fingerprint != builder.build(with_export).structural_fingerprint


def test_a_different_route_is_a_different_state():
    """Dos rutas distintas nunca comparten identidad estructural."""
    builder = StateSignatureBuilder()

    other = _screen("ACT-001 Inspeccion Pendiente ACT-002 Permiso Aprobado")
    other["path"] = "/admin/tramites/categorias"

    assert (
        builder.build(PAGE_ONE).structural_fingerprint
        != builder.build(other).structural_fingerprint
    )


# --------------------------------------------------------------------------
# 4. Clasificación del efecto de un evento
# --------------------------------------------------------------------------


def test_pagination_is_classified_as_content_change():
    """El caso focal: "Siguiente página" produce efecto sin crear estado."""
    builder = StateSignatureBuilder()
    before = builder.build(PAGE_ONE)
    after = builder.build(PAGE_TWO)

    effect = classify_event_effect(
        before_route=before.route,
        before_structural_fingerprint=before.structural_fingerprint,
        before_exact_fingerprint=before.exact_fingerprint,
        after=after,
    )

    assert effect is EventEffect.CONTENT_CHANGE
    assert effect.creates_state is False


def test_no_observable_difference_is_no_effect():
    builder = StateSignatureBuilder()
    before = builder.build(PAGE_ONE)
    after = builder.build(PAGE_ONE)

    effect = classify_event_effect(
        before_route=before.route,
        before_structural_fingerprint=before.structural_fingerprint,
        before_exact_fingerprint=before.exact_fingerprint,
        after=after,
    )

    assert effect is EventEffect.NO_EFFECT
    assert effect.creates_state is False


def test_structural_difference_creates_a_state():
    builder = StateSignatureBuilder()
    before = builder.build(PAGE_ONE)

    with_dialog = _screen("ACT-001 Inspeccion")
    with_dialog["dialogs"] = [
        {"title": "Detalle", "role": "dialog", "open": True}
    ]
    after = builder.build(with_dialog)

    effect = classify_event_effect(
        before_route=before.route,
        before_structural_fingerprint=before.structural_fingerprint,
        before_exact_fingerprint=before.exact_fingerprint,
        after=after,
    )

    assert effect is EventEffect.STRUCTURAL_CHANGE
    assert effect.creates_state is True


def test_route_difference_creates_a_state():
    builder = StateSignatureBuilder()
    before = builder.build(PAGE_ONE)

    elsewhere = _screen("ACT-001 Inspeccion")
    elsewhere["path"] = "/admin/tramites/categorias"
    after = builder.build(elsewhere)

    effect = classify_event_effect(
        before_route=before.route,
        before_structural_fingerprint=before.structural_fingerprint,
        before_exact_fingerprint=before.exact_fingerprint,
        after=after,
    )

    assert effect is EventEffect.ROUTE_CHANGE
    assert effect.creates_state is True


# --------------------------------------------------------------------------
# 5. El canónico no debe reportar que un self-loop "no cambió nada"
# --------------------------------------------------------------------------


def test_canonical_self_loop_keeps_the_event_as_effective():
    """Un self-loop CONTENT_CHANGE sí produjo efecto observable.

    Si `changed` se derivara de `source.id != target.id`, M2 recibiría
    evidencia diciendo que "Siguiente página" no hace nada.
    """
    transition = CanonicalTransition(
        id="transition:demo",
        source_state_id="ui_state:a",
        target_state_id="ui_state:a",
        event_id="event:next-page",
        category="change_pagination",
        changed=True,
        effect=str(EventEffect.CONTENT_CHANGE),
        route_changed=False,
    )

    assert transition.source_state_id == transition.target_state_id
    assert transition.changed is True
    assert transition.effect == "CONTENT_CHANGE"
