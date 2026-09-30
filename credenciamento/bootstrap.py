"""Preparação do banco ao iniciar qualquer comando."""
from .csv_io import export_csv
from .db import connect, create_schema
from .desks import apply_desk_config, configured_ranges, mark_manual_desks, priority_guiche, validate_ranges


def init_db():
    with connect() as db:
        if "guiche_manual" in create_schema(db):
            mark_manual_desks(db)
    try:
        ranges = validate_ranges(configured_ranges())
    except ValueError:
        return
    # Ao iniciar, junta restos de renomeações antigas e leva prioritários ao guichê de prioridade.
    if apply_desk_config(ranges, priority_guiche(), ranges, priority_guiche(), "system"):
        export_csv()
