"""Minimum rescan usage over accepted V2 budget evidence.

Each measured public port carries a node cost of one rescan when its pixel
is below the issue threshold, otherwise zero.  Station-provided links give
the minimum number of below-threshold interior pixels of a tile path.
Permitted seams connect facing ports across tiles for free.  The minimum
node-weighted walk between the two probe ports is solved with Dijkstra over
the public-port graph; a port pair that names the same physical port is a
single-pixel trace.
"""
import heapq

from analysis.seams import enumerate_seam_links
from intake.budget_evidence import BudgetEvidence
from survey.layout import PortRef, SurveyLayout


def minimum_rescan(layout: SurveyLayout, evidences: tuple[BudgetEvidence, ...],
                   start: PortRef, finish: PortRef) -> int | None:
    node_cost: dict[PortRef, int] = {}
    for evidence in evidences:
        for port, cost in evidence.port_cost.items():
            node_cost[port] = cost
    if start not in node_cost or finish not in node_cost:
        return None

    edges: dict[PortRef, dict[PortRef, int]] = {port: {} for port in node_cost}
    for evidence in evidences:
        for port_a, port_b, interior in evidence.links:
            directed = interior + node_cost[port_b]
            edges[port_a][port_b] = min(edges[port_a].get(port_b, 10 ** 12), directed)
            reverse = interior + node_cost[port_a]
            edges[port_b][port_a] = min(edges[port_b].get(port_a, 10 ** 12), reverse)
    for link in enumerate_seam_links(layout):
        if link.a in node_cost and link.b in node_cost:
            edges[link.a][link.b] = min(edges[link.a].get(link.b, 10 ** 12), node_cost[link.b])
            edges[link.b][link.a] = min(edges[link.b].get(link.a, 10 ** 12), node_cost[link.a])

    distances = {start: node_cost[start]}
    frontier = [(distances[start], start)]
    while frontier:
        current_cost, port = heapq.heappop(frontier)
        if current_cost != distances[port]:
            continue
        if port == finish:
            return current_cost
        for neighbor, edge_cost in edges[port].items():
            candidate = current_cost + edge_cost
            if candidate < distances.get(neighbor, 10 ** 12):
                distances[neighbor] = candidate
                heapq.heappush(frontier, (candidate, neighbor))
    return None
