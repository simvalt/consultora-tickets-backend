"""Estatus y transiciones de tickets (definidos en código)."""

SIN_INICIO, INICIADO, EN_PROCESO, PENDIENTE, CANCELADO, CERRADO, RECHAZADO = (
    "sin_inicio", "iniciado", "en_proceso", "pendiente", "cancelado", "cerrado", "rechazado",
)
STATUS_LABELS = {
    SIN_INICIO: "Sin inicio", INICIADO: "Iniciado", EN_PROCESO: "En proceso", PENDIENTE: "Pendiente",
    CANCELADO: "Cancelado", CERRADO: "Cerrado", RECHAZADO: "Rechazado",
}
STATUSES = tuple(STATUS_LABELS)
OPEN_STATUSES = (SIN_INICIO, INICIADO, EN_PROCESO, PENDIENTE)
PRIORITIES = ("baja", "media", "alta", "critica")
PRIORITY_LABELS = {"baja": "Baja", "media": "Media", "alta": "Alta", "critica": "Crítica"}

TRANSITIONS: dict[str, tuple[str, ...]] = {
    SIN_INICIO: (INICIADO, CANCELADO, RECHAZADO),
    INICIADO: (EN_PROCESO, PENDIENTE, CANCELADO),
    EN_PROCESO: (PENDIENTE, CERRADO, CANCELADO),
    PENDIENTE: (EN_PROCESO, CANCELADO),
    CERRADO: (EN_PROCESO,),  # reapertura
    CANCELADO: (),
    RECHAZADO: (),
}
REASON_REQUIRED = {PENDIENTE, CANCELADO, RECHAZADO}
ASSIGNEE_REQUIRED = {INICIADO, EN_PROCESO, PENDIENTE, CERRADO}


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, ())


def is_reopen(current: str, new: str) -> bool:
    return current == CERRADO and new == EN_PROCESO
