from collections import deque
import json

from analysis.request import ReviewRequest
from analysis.seams import enumerate_seam_links
from intake.selection import AcceptedSet
from survey.layout import PortRef


def _read_port(tile_id, position):
    if (not isinstance(position, list) or len(position) != 2
            or any(type(coord) is not int for coord in position)):
        raise ValueError('invalid coordinate in station evidence')
    return PortRef(tile_id, *position)


def _read_groups(tile_id, raw):
    if not isinstance(raw, list):
        raise ValueError('invalid station groups')
    groups, membership = [], {}
    for group in raw:
        if not isinstance(group, list) or not group:
            raise ValueError('station group must contain an active port')
        ports = tuple(_read_port(tile_id, position) for position in group)
        for port in ports:
            if port in membership:
                raise ValueError('port occurs twice at a station threshold')
            membership[port] = len(groups)
        groups.append(ports)
    return groups, membership


def _groups_at(envelope, allowed_ports, threshold):
    raw = json.loads(envelope.body)
    if not isinstance(raw, dict) or set(raw) != {'ports', 'groups', 'changes'}:
        raise ValueError('unrecognized station evidence')
    if not isinstance(raw['ports'], list) or not isinstance(raw['changes'], list):
        raise ValueError('invalid station evidence arrays')
    scores = {}
    for entry in raw['ports']:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError('invalid scored station port')
        port = _read_port(envelope.tile_id, entry[:2])
        score = entry[2]
        if (port in scores or port not in allowed_ports
                or type(score) is not int or not 0 <= score <= 100):
            raise ValueError('invalid station port or score')
        scores[port] = score

    levels = [(0, raw['groups'])]
    for change in raw['changes']:
        if (not isinstance(change, list) or len(change) != 2
                or type(change[0]) is not int or not levels[-1][0] < change[0] <= 100):
            raise ValueError('invalid station evidence change threshold')
        levels.append(tuple(change))

    previous = None
    chosen = []
    for index, (floor, raw_groups) in enumerate(levels):
        groups, membership = _read_groups(envelope.tile_id, raw_groups)
        last = levels[index + 1][0] - 1 if index + 1 < len(levels) else 100
        if set(membership) != {port for port, score in scores.items() if score >= floor}:
            raise ValueError('station groups disagree with active port scores')
        if any(scores[port] < last for port in membership):
            raise ValueError('active station port outlasts its score')
        if previous is not None and any(len({previous[port] for port in group}) > 1 for group in groups):
            raise ValueError('station groups merge at a higher threshold')
        if floor <= threshold <= last:
            chosen = groups
        previous = membership
    return chosen


def connected_at_threshold(accepted: AcceptedSet, request: ReviewRequest, threshold: int) -> bool:
    if type(threshold) is not int or not 0 <= threshold <= 100:
        raise ValueError('threshold must be an integer from 0 to 100')
    graph = {}
    for packet in accepted.packets:
        envelope = packet.envelope
        allowed_ports = set(accepted.layout.ports_for(envelope.tile_id))
        for group in _groups_at(envelope, allowed_ports, threshold):
            head, *rest = group
            graph[head] = set(rest)
            for port in rest:
                graph[port] = {head}
    for link in enumerate_seam_links(accepted.layout):
        if link.a in graph and link.b in graph:
            graph[link.a].add(link.b)
            graph[link.b].add(link.a)

    if request.start not in graph or request.finish not in graph:
        return False
    frontier = deque([request.start])
    seen = {request.start}
    while frontier:
        port = frontier.popleft()
        if port == request.finish:
            return True
        for neighbor in graph[port] - seen:
            seen.add(neighbor)
            frontier.append(neighbor)
    return False
