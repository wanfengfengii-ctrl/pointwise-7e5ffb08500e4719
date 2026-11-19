import sqlite3

from evidence.budget_body import build_budget_body
from evidence.packets import IssuedPacket, record_issued_packet
from evidence.terminals import inspect_terminals
from survey.catalog import load_survey
from survey.layout import layout_digest
from survey.revisions import load_revision


def _positions(groups):
    return [[(port.x, port.y) for port in group] for group in groups]


def _packet_body(layout, revision):
    tile = layout.tile(revision.tile_id)
    base = inspect_terminals(layout, revision, 0)
    scores = []
    for port in sorted(base.active):
        row, col = tile.to_local(port.x, port.y)
        scores.append((port.x, port.y, revision.grid[row][col]))

    changes = []
    previous = base.groups
    for threshold in range(1, 101):
        groups = inspect_terminals(layout, revision, threshold).groups
        if groups != previous:
            changes.append((threshold, _positions(groups)))
            previous = groups
    return {'ports': scores, 'groups': _positions(base.groups), 'changes': changes}


def issue(db: sqlite3.Connection, revision_id: str) -> IssuedPacket:
    revision = load_revision(db, revision_id)
    layout = load_survey(db, revision.survey_id)
    return record_issued_packet(db, revision, layout_digest(layout), _packet_body(layout, revision))


def issue_budget(db: sqlite3.Connection, revision_id: str, threshold: int) -> IssuedPacket:
    if type(threshold) is not int or not 0 <= threshold <= 100:
        raise ValueError('threshold must be an integer from 0 to 100')
    revision = load_revision(db, revision_id)
    layout = load_survey(db, revision.survey_id)
    body = build_budget_body(layout, revision, threshold)
    return record_issued_packet(db, revision, layout_digest(layout), body, version=2)
