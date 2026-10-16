from app import workflow as wf
from app.csvutil import safe_cell


def test_transition_table_matches_specification():
    assert wf.TRANSITIONS == {
        "sin_inicio": ("iniciado", "cancelado", "rechazado"),
        "iniciado": ("en_proceso", "pendiente", "cancelado"),
        "en_proceso": ("pendiente", "cerrado", "cancelado"),
        "pendiente": ("en_proceso", "cancelado"),
        "cerrado": ("en_proceso",),
        "cancelado": (),
        "rechazado": (),
    }
    assert wf.is_reopen("cerrado", "en_proceso") and not wf.is_reopen("pendiente", "en_proceso")
    assert set(wf.OPEN_STATUSES) == {"sin_inicio", "iniciado", "en_proceso", "pendiente"}


def test_safe_cell():
    assert safe_cell(None) == "" and safe_cell(True) == "Sí" and safe_cell(7) == "7"
    for bad in ("=1+1", "+1", "-1", "@x", "\tx", "\rx"):
        assert safe_cell(bad) == "'" + bad
    assert safe_cell("normal") == "normal"
