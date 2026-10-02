"""Preparação do banco ao iniciar qualquer comando."""
from .csv_io import export_csv
from .db import connect, create_schema
from .desks import apply_desk_config, configured_ranges, mark_manual_desks, priority_guiche, validate_ranges


def init_db():
    with connect() as db:
        added = create_schema(db)
        if "guiche_manual" in added:
            mark_manual_desks(db)
    if "lookup_keys" in added:
        # O espelho CSV antigo tinha CPF e e-mail em texto: reescreve já, sem eles.
        export_csv()
    try:
        ranges = validate_ranges(configured_ranges())
    except ValueError:
        return
    # Ao iniciar, junta restos de renomeações antigas e leva prioritários ao guichê de prioridade.
    if apply_desk_config(ranges, priority_guiche(), ranges, priority_guiche(), "system"):
        export_csv()
