"""Station-side construction of V2 budget evidence.

The center only receives the registered public ports and their cost
relations.  Non-public pixel coordinates, per-point scores and per-point
records stay on the station.  For every pair of measured public ports the
station records the minimum number of below-threshold pixels strictly
interior to a four-neighbour tile path between them, which is a metric
closure that lets the center re-derive the full-tile minimum usage together
with the public port costs and the permitted seams.
"""
from collections import deque

from survey.layout import SurveyLayout
from survey.revisions import FrozenRevision


def _require_threshold(threshold):
    if type(threshold) is not int or not 0 <= threshold <= 100:
        raise ValueError('threshold must be an integer from 0 to 100')


def cell_weight(grid, row, col, threshold):
    score = grid[row][col]
    if score is None:
        return None
    return 0 if score >= threshold else 1


def build_budget_body(layout: SurveyLayout, revision: FrozenRevision,
                      threshold: int) -> dict:
    _require_threshold(threshold)
    if revision.survey_id != layout.survey_id:
        raise ValueError('revision belongs to another survey')
    tile = layout.tile(revision.tile_id)
    grid = revision.grid
    height, width = tile.height, tile.width

    measured = []
    for port in layout.ports_for(revision.tile_id):
        row, col = tile.to_local(port.x, port.y)
        weight = cell_weight(grid, row, col, threshold)
        if weight is not None:
            measured.append((port, (row, col), weight))

    ports_payload = [[port.x, port.y, weight] for port, _, weight in measured]
    links_payload = []
    for source_index, (source_port, source_cell, _) in enumerate(measured):
        distances = {source_cell: 0}
        frontier = deque([source_cell])
        while frontier:
            row, col = frontier.popleft()
            current = distances[(row, col)]
            for next_row, next_col in ((row - 1, col), (row + 1, col),
                                       (row, col - 1), (row, col + 1)):
                if not (0 <= next_row < height and 0 <= next_col < width):
                    continue
                weight = cell_weight(grid, next_row, next_col, threshold)
                if weight is None:
                    continue
                candidate = current + weight
                if candidate < distances.get((next_row, next_col), 10 ** 12):
                    distances[(next_row, next_col)] = candidate
                    if weight == 0:
                        frontier.appendleft((next_row, next_col))
                    else:
                        frontier.append((next_row, next_col))
        for target_index in range(source_index + 1, len(measured)):
            target_port, target_cell, target_weight = measured[target_index]
            if target_cell not in distances:
                continue
            interior_cost = distances[target_cell] - target_weight
            links_payload.append([[source_port.x, source_port.y],
                                  [target_port.x, target_port.y],
                                  interior_cost])
    return {'threshold': threshold,
            'evidence': {'ports': ports_payload, 'links': links_payload}}
