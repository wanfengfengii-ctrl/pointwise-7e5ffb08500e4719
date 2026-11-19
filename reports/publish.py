import sqlite3

from analysis.budget_path import minimum_rescan
from analysis.decision import evaluate_review
from analysis.request import validate_request
from intake.budget_evidence import parse_budget_evidence
from intake.coverage import validate_coverage
from intake.records import load_acceptance, load_accepted_layout
from intake.selection import select_accepted_set
from reports.archive import (BudgetDecision, BudgetReport, ReportRecord,
                             commit_budget_report, commit_report)
from survey.layout import layout_digest


def review_issue(db: sqlite3.Connection, survey_id: str, acceptance_ids,
                 probe_a: str, probe_b: str, threshold: int) -> ReportRecord:
    accepted = select_accepted_set(db, survey_id, acceptance_ids)
    request = validate_request(accepted.layout, probe_a, probe_b, threshold)
    decision = evaluate_review(accepted, request)
    return commit_report(db, accepted.layout.survey_id, layout_digest(accepted.layout),
                         accepted.parents, decision)


def _select_budget_set(db: sqlite3.Connection, survey_id: str, acceptance_ids):
    if not isinstance(acceptance_ids, (list, tuple)):
        raise ValueError('provide a list or tuple of acceptance identities')
    try:
        layout = load_accepted_layout(db, survey_id)
        packets = tuple(load_acceptance(db, item) for item in acceptance_ids)
    except KeyError as error:
        raise ValueError('survey or packet has not been accepted') from error
    validate_coverage(layout, packets)
    ordered = tuple(sorted(packets, key=lambda packet: packet.envelope.tile_id))
    if any(packet.envelope.version != 2 for packet in ordered):
        raise ValueError('budget issue requires version 2 station packets')
    evidences = tuple(parse_budget_evidence(layout, packet.envelope) for packet in ordered)
    thresholds = {evidence.threshold for evidence in evidences}
    if len(thresholds) != 1:
        raise ValueError('budget packets must share one fixed threshold')
    return layout, ordered, evidences, thresholds.pop()


def budget_issue(db: sqlite3.Connection, survey_id: str, acceptance_ids,
                 probe_a: str, probe_b: str, budget: int) -> BudgetReport:
    if type(budget) is not int or budget < 0:
        raise ValueError('budget must be a non-negative integer')
    layout, packets, evidences, threshold = _select_budget_set(db, survey_id, acceptance_ids)
    try:
        start, finish = layout.probe(probe_a), layout.probe(probe_b)
    except KeyError as error:
        raise ValueError('probe is not registered in the survey') from error
    minimum = minimum_rescan(layout, evidences, start, finish)
    passed = minimum is not None and minimum <= budget
    decision = BudgetDecision(probe_a, probe_b, threshold, budget, minimum, passed)
    parents = tuple(packet.receipt for packet in packets)
    return commit_budget_report(db, layout.survey_id, layout_digest(layout), parents, decision)
