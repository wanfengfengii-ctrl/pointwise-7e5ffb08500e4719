"""Strict parsing of V2 budget evidence accepted by the center."""
from dataclasses import dataclass
import json

from intake.envelope import PacketEnvelope
from survey.layout import PortRef, SurveyLayout


@dataclass(frozen=True)
class BudgetEvidence:
    tile_id: str
    threshold: int
    port_cost: dict[PortRef, int]
    links: tuple[tuple[PortRef, PortRef, int], ...]


def _position(value):
    if not isinstance(value, list) or len(value) != 2 or any(type(coord) is not int for coord in value):
        raise ValueError('invalid coordinate in budget evidence')
    return value[0], value[1]


def parse_budget_evidence(layout: SurveyLayout, envelope: PacketEnvelope) -> BudgetEvidence:
    if envelope.version != 2:
        raise ValueError('budget issue requires a version 2 station packet')
    try:
        raw = json.loads(envelope.body)
    except ValueError as error:
        raise ValueError('invalid budget evidence JSON') from error
    if not isinstance(raw, dict) or set(raw) != {'threshold', 'evidence'}:
        raise ValueError('invalid budget packet body')
    threshold = raw['threshold']
    if type(threshold) is not int or not 0 <= threshold <= 100:
        raise ValueError('invalid budget threshold')
    evidence = raw['evidence']
    if not isinstance(evidence, dict) or set(evidence) != {'ports', 'links'}:
        raise ValueError('invalid budget evidence object')

    allowed = set(layout.ports_for(envelope.tile_id))
    port_cost = {}
    if not isinstance(evidence['ports'], list):
        raise ValueError('invalid budget port list')
    for entry in evidence['ports']:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError('invalid budget port entry')
        x, y = _position(entry[:2])
        cost = entry[2]
        port = PortRef(envelope.tile_id, x, y)
        if port not in allowed or port in port_cost or type(cost) is not int or cost not in (0, 1):
            raise ValueError('invalid budget port or cost')
        port_cost[port] = cost

    links = []
    seen_pairs = set()
    if not isinstance(evidence['links'], list):
        raise ValueError('invalid budget link list')
    for entry in evidence['links']:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError('invalid budget link entry')
        a_x, a_y = _position(entry[0])
        b_x, b_y = _position(entry[1])
        cost = entry[2]
        port_a, port_b = PortRef(envelope.tile_id, a_x, a_y), PortRef(envelope.tile_id, b_x, b_y)
        if (port_a not in port_cost or port_b not in port_cost or port_a == port_b
                or type(cost) is not int or cost < 0):
            raise ValueError('invalid budget link endpoints or cost')
        pair = tuple(sorted((port_a, port_b)))
        if pair in seen_pairs:
            raise ValueError('duplicate budget link')
        seen_pairs.add(pair)
        links.append((port_a, port_b, cost))
    return BudgetEvidence(envelope.tile_id, threshold, port_cost, tuple(links))
