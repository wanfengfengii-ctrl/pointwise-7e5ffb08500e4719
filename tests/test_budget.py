import heapq
import json
import sqlite3

from analysis.budget_path import minimum_rescan
from budget_examples import revision_pair, seam_pair
from demo import load_sample
from evidence.issue import issue, issue_budget
from evidence.readback import read_station_packet
from intake.accept import accept
from intake.budget_evidence import parse_budget_evidence
from intake.records import load_acceptance
from reports.publish import budget_issue, review_issue
from reports.readback import retrieve, retrieve_budget
from survey.catalog import register
from survey.freeze import freeze_grid
from survey.layout import PortRef, SurveyLayout, TileBounds
from tests.support import ProjectCase, counts

BUDGET_TABLES = ('budget_reports', 'budget_receipts')


def oracle_minimum(layout, grids, probe_a, probe_b, threshold):
    """Independent full-grid node-weighted shortest path, no evidence use."""
    weights = {}
    tile_of = {}
    for tile in layout.tiles:
        for row, scores in enumerate(grids[tile.tile_id]):
            for col, score in enumerate(scores):
                if score is None:
                    continue
                position = (tile.x + col, tile.y + row)
                weights[position] = 0 if score >= threshold else 1
                tile_of[position] = tile.tile_id
    start = (layout.probe(probe_a).x, layout.probe(probe_a).y)
    finish = (layout.probe(probe_b).x, layout.probe(probe_b).y)
    if start not in weights or finish not in weights:
        return None
    seams = {frozenset(((a.x, a.y), (b.x, b.y))) for a, b in layout.seams}
    frontier = [(weights[start], start)]
    distances = {start: weights[start]}
    while frontier:
        cost, position = heapq.heappop(frontier)
        if cost != distances[position]:
            continue
        if position == finish:
            return cost
        x, y = position
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if (nx, ny) not in weights:
                continue
            if tile_of[(nx, ny)] != tile_of[position] and frozenset((position, (nx, ny))) not in seams:
                continue
            candidate = cost + weights[(nx, ny)]
            if candidate < distances.get((nx, ny), 10 ** 12):
                distances[(nx, ny)] = candidate
                heapq.heappush(frontier, (candidate, (nx, ny)))
    return None


class BudgetIssueTests(ProjectCase):
    def _issue_v2(self, layout, tile_id, rows, threshold):
        revision = freeze_grid(self.station, layout.survey_id, tile_id, rows)
        return issue_budget(self.station, revision, threshold)

    def _provision_v2(self, layout, grids, threshold):
        register(self.station, layout)
        packets = {tile: self._issue_v2(layout, tile, rows, threshold)
                   for tile, rows in grids.items()}
        return {tile: accept(self.center, layout, packet.payload)
                for tile, packet in packets.items()}

    def test_revision_pair_table(self):
        layout, versions = revision_pair()
        for index, expected in (((0, (1, True)), (1, (2, False)))):
            receipts = self._provision_v2(layout, versions[index], 6)
            report = budget_issue(self.center, layout.survey_id,
                                  [item.acceptance_id for item in receipts.values()], 'a', 'b', 1)
            self.assertEqual((report.decision.minimum, report.decision.passed), expected)
            self.assertEqual(report.decision.threshold, 6)
            self.assertEqual(report.decision.budget, 1)
            self.reopen_center()
            self.assertEqual(retrieve_budget(self.center, report.report_id), report)

    def test_seam_pair_table_and_no_path(self):
        layout, versions = seam_pair()
        first = self._provision_v2(layout, versions[0], 6)
        ids = [item.acceptance_id for item in first.values()]
        across = budget_issue(self.center, layout.survey_id, ids, 'a', 'b', 2)
        self.assertEqual((across.decision.minimum, across.decision.passed), (2, True))
        single = budget_issue(self.center, layout.survey_id, ids, 'edge', 'edge', 1)
        self.assertEqual((single.decision.minimum, single.decision.passed), (1, True))
        self.assertEqual(single.receipt.packet_ids,
                         tuple(parent.packet_id for parent in single.parents))
        corrected = self._provision_v2(layout, versions[1], 6)
        report = budget_issue(self.center, layout.survey_id,
                              [item.acceptance_id for item in corrected.values()], 'a', 'b', 2)
        self.assertEqual((report.decision.minimum, report.decision.passed), (None, False))
        self.assertFalse(retrieve_budget(self.center, report.report_id).decision.passed)

    def test_sample_initial_and_correction(self):
        layout, grids, correction = load_sample()
        receipts = self._provision_v2(layout, grids, 5)
        ids = [item.acceptance_id for item in receipts.values()]
        free = budget_issue(self.center, layout.survey_id, ids, 'top', 'bottom', 0)
        self.assertEqual((free.decision.minimum, free.decision.passed), (0, True))
        corrected = self._issue_v2(layout, correction['tile_id'], correction['rows'], 5)
        new_east = accept(self.center, layout, corrected.payload)
        chosen = dict(receipts)
        chosen[correction['tile_id']] = new_east
        charged = budget_issue(self.center, layout.survey_id,
                               [item.acceptance_id for item in chosen.values()], 'top', 'bottom', 0)
        self.assertEqual((charged.decision.minimum, charged.decision.passed), (1, False))
        self.assertEqual(retrieve_budget(self.center, free.report_id), free)
        self.assertEqual(len(free.parents), 2)
        self.assertEqual(tuple(parent.tile_id for parent in free.parents), ('east', 'west'))

    def test_equality_budget_passes_at_exact_budget_and_zero_fee(self):
        layout = revision_pair()[0]
        receipts = self._provision_v2(layout, {'line': [[7, 1, 7, 7]]}, 6)
        ids = [item.acceptance_id for item in receipts.values()]
        exact = budget_issue(self.center, layout.survey_id, ids, 'a', 'b', 1)
        self.assertTrue(exact.decision.passed)
        zero_fee_layout = SurveyLayout(
            'zero', (TileBounds('line', 0, 0, 2, 1),),
            (('a', PortRef('line', 0, 0)), ('b', PortRef('line', 1, 0))))
        zero_receipts = self._provision_v2(zero_fee_layout, {'line': [[7, 7]]}, 6)
        zero = budget_issue(self.center, zero_fee_layout.survey_id,
                            [item.acceptance_id for item in zero_receipts.values()], 'a', 'b', 0)
        self.assertEqual((zero.decision.minimum, zero.decision.passed), (0, True))

    def test_colocated_probe_single_pixel_trace(self):
        layout = SurveyLayout(
            'solo', (TileBounds('one', 0, 0, 1, 1),),
            (('a', PortRef('one', 0, 0)), ('b', PortRef('one', 0, 0))))
        for rows, expected in (([[7]], (0, True)), ([[1]], (1, True)), ([[None]], (None, False))):
            receipts = self._provision_v2(layout, {'one': rows}, 6)
            report = budget_issue(self.center, layout.survey_id,
                                  [item.acceptance_id for item in receipts.values()], 'a', 'b', 1)
            self.assertEqual((report.decision.minimum, report.decision.passed), expected)

    def test_threshold_and_budget_reject_booleans(self):
        layout, versions = revision_pair()
        register(self.station, layout)
        revision = freeze_grid(self.station, layout.survey_id, 'line', versions[0]['line'])
        for bad in (True, False, -1, 101, 0.0, '6'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                issue_budget(self.station, revision, bad)
        packet = issue_budget(self.station, revision, 6)
        ack = accept(self.center, layout, packet.payload)
        for bad in (True, False, -1, 0.0, '1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                budget_issue(self.center, layout.survey_id, [ack.acceptance_id], 'a', 'b', bad)
        with self.assertRaises(KeyError):
            issue_budget(self.station, '0' * 32, 6)

    def test_bad_v2_packet_rejected_without_records(self):
        layout, versions = revision_pair()
        register(self.station, layout)
        revision = freeze_grid(self.station, layout.survey_id, 'line', versions[0]['line'])
        packet = issue_budget(self.station, revision, 6)
        wire = json.loads(packet.payload)
        bad_link = [[[0, 0], [3, 0], -1]]
        bad_payloads = (
            json.dumps(dict(wire, body=[])),
            json.dumps(dict(wire, body={'threshold': 6})),
            json.dumps(dict(wire, body={'threshold': 6, 'evidence': {}})),
            json.dumps(dict(wire, body={'threshold': True,
                                        'evidence': {'ports': [], 'links': []}})),
            json.dumps(dict(wire, body={'threshold': 6,
                                        'evidence': {'ports': [['x', 0, 0]], 'links': []}})),
            json.dumps(dict(wire, body={'threshold': 6,
                                        'evidence': {'ports': [[0, 0, 2]], 'links': []}})),
            json.dumps(dict(wire, body={'threshold': 6,
                                        'evidence': {'ports': [[9, 0, 0]], 'links': []}})),
            json.dumps(dict(wire, body={'threshold': 6,
                                        'evidence': {'ports': [[0, 0, 0]], 'links': bad_link}})),
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload[:30]), self.assertRaises(ValueError):
                accept(self.center, layout, payload)
        self.assertEqual(counts(self.center, ('accepted_packets',)), (0,))
        ack = accept(self.center, layout, packet.payload)
        self.assertEqual(accept(self.center, layout, packet.payload), ack)
        tampered = json.loads(packet.payload)
        tampered['body']['evidence']['ports'] = []
        with self.assertRaises(ValueError):
            accept(self.center, layout, json.dumps(tampered))

    def test_mixed_versions_and_thresholds_rejected(self):
        layout, versions = revision_pair()
        register(self.station, layout)
        revision = freeze_grid(self.station, layout.survey_id, 'line', versions[0]['line'])
        v2 = issue_budget(self.station, revision, 6)
        v1 = issue(self.station, revision)
        ack_v2 = accept(self.center, layout, v2.payload)
        ack_v1 = accept(self.center, layout, v1.payload)
        with self.assertRaises(ValueError):
            budget_issue(self.center, layout.survey_id, [ack_v1.acceptance_id], 'a', 'b', 5)
        with self.assertRaises(ValueError):
            review_issue(self.center, layout.survey_id, [ack_v2.acceptance_id], 'a', 'b', 6)
        other = issue_budget(self.station, revision, 5)
        ack_other = accept(self.center, layout, other.payload)
        # same tile can only appear once anyway; mixed thresholds across tiles below
        self.assertIsNotNone(ack_other)

    def test_mixed_threshold_across_tiles_rejected(self):
        layout, versions = seam_pair()
        register(self.station, layout)
        packets = []
        for index, (tile, rows) in enumerate(versions[0].items()):
            revision = freeze_grid(self.station, layout.survey_id, tile, rows)
            packets.append(issue_budget(self.station, revision, 6 if index == 0 else 5))
        acks = [accept(self.center, layout, packet.payload).acceptance_id for packet in packets]
        with self.assertRaises(ValueError):
            budget_issue(self.center, layout.survey_id, acks, 'a', 'b', 5)

    def test_coverage_and_identity_errors(self):
        layout, versions = revision_pair()
        receipts = self._provision_v2(layout, versions[0], 6)
        ids = [item.acceptance_id for item in receipts.values()]
        other = SurveyLayout('other', (TileBounds('line', 0, 0, 4, 1),),
                             (('a', PortRef('line', 0, 0)), ('b', PortRef('line', 3, 0))))
        other_receipts = self._provision_v2(other, {'line': [[7, 7, 7, 7]]}, 6)
        other_id = next(iter(other_receipts.values())).acceptance_id
        cases = (
            lambda: budget_issue(self.center, layout.survey_id, [], 'a', 'b', 5),
            lambda: budget_issue(self.center, layout.survey_id, ids * 2, 'a', 'b', 5),
            lambda: budget_issue(self.center, layout.survey_id, [other_id], 'a', 'b', 5),
            lambda: budget_issue(self.center, layout.survey_id, [999], 'a', 'b', 5),
            lambda: budget_issue(self.center, 'missing-survey', ids, 'a', 'b', 5),
            lambda: budget_issue(self.center, layout.survey_id, ids, 'a', 'ghost', 5),
            lambda: budget_issue(self.center, layout.survey_id, ids, 'ghost', 'b', 5),
            lambda: budget_issue(self.center, layout.survey_id, set(ids), 'a', 'b', 5),
        )
        for call in cases:
            with self.subTest(), self.assertRaises(ValueError):
                call()
        self.assertEqual(counts(self.center, BUDGET_TABLES), (0, 0))

    def test_failed_commit_rolls_back_budget_records_and_retry(self):
        layout, versions = revision_pair()
        receipts = self._provision_v2(layout, versions[0], 6)
        ids = [item.acceptance_id for item in receipts.values()]
        first = budget_issue(self.center, layout.survey_id, ids, 'a', 'b', 5)
        self.center.executescript('''
            PRAGMA foreign_keys = ON;
            CREATE TABLE budget_guard (id INTEGER PRIMARY KEY);
            CREATE TABLE budget_probe (
                key INTEGER REFERENCES budget_guard(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER refuse_budget_commit AFTER INSERT ON budget_receipts
            BEGIN INSERT INTO budget_probe VALUES (1); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            budget_issue(self.center, layout.survey_id, ids, 'a', 'b', 5)
        self.reopen_center()
        self.assertEqual(counts(self.center, BUDGET_TABLES + ('budget_probe',)), (1, 1, 0))
        self.assertEqual(retrieve_budget(self.center, first.report_id), first)
        self.assertIsNone(retrieve_budget(self.center, first.report_id + 1))
        self.center.execute('DROP TRIGGER refuse_budget_commit')
        retry = budget_issue(self.center, layout.survey_id, ids, 'a', 'b', 5)
        self.assertEqual(retry.decision.minimum, 1)
        self.assertEqual(counts(self.center, BUDGET_TABLES), (2, 2))

    def test_readback_validation_and_amendment_keeps_old_value(self):
        layout, grids, correction = load_sample()
        receipts = self._provision_v2(layout, grids, 5)
        first = budget_issue(self.center, layout.survey_id,
                             [item.acceptance_id for item in receipts.values()], 'top', 'bottom', 5)
        corrected = self._issue_v2(layout, correction['tile_id'], correction['rows'], 5)
        chosen = dict(receipts)
        chosen[correction['tile_id']] = accept(self.center, layout, corrected.payload)
        second = budget_issue(self.center, layout.survey_id,
                              [item.acceptance_id for item in chosen.values()], 'top', 'bottom', 5)
        for bad in (0, 2 ** 63, True, 1.5, '7'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                retrieve_budget(self.center, bad)
        self.reopen_center()
        self.assertEqual(retrieve_budget(self.center, first.report_id), first)
        self.assertEqual(retrieve_budget(self.center, second.report_id), second)
        self.assertIsNone(retrieve_budget(self.center, second.report_id + 100))
        self.assertEqual(first.decision.minimum, 0)
        self.assertEqual(second.decision.minimum, 1)
        self.assertEqual(first.parents, receipts_tuple(receipts))

    def test_station_packet_readback_and_private_pixels_absent(self):
        layout, grids, _ = load_sample()
        register(self.station, layout)
        packet = self._issue_v2(layout, 'east', grids['east'], 5)
        self.reopen_station()
        self.assertEqual(read_station_packet(self.station, packet.packet_id), packet)
        wire = json.loads(packet.payload)
        self.assertEqual(wire['version'], 2)
        self.assertEqual(set(wire['body']), {'threshold', 'evidence'})
        # only registered public ports leave the station
        public = {(port.x, port.y) for port in layout.ports_for('east')}
        self.assertEqual(public, {(x, y) for x, y, _ in wire['body']['evidence']['ports']})
        # no per-point scores, only 0/1 cost roles
        self.assertTrue(all(cost in (0, 1) for _, _, cost in wire['body']['evidence']['ports']))

    def test_reinitialize_keeps_budget_records(self):
        layout, versions = revision_pair()
        receipts = self._provision_v2(layout, versions[0], 6)
        report = budget_issue(self.center, layout.survey_id,
                              [item.acceptance_id for item in receipts.values()], 'a', 'b', 1)
        self.reopen_center()
        from demo import initialize_center
        for _ in range(2):
            initialize_center(self.center)
        self.reopen_center()
        self.assertEqual(counts(self.center, BUDGET_TABLES), (1, 1))
        self.assertEqual(retrieve_budget(self.center, report.report_id), report)
        # V1 readback remains available alongside V2
        self.assertIsNone(retrieve(self.center, report.report_id + 10))


def receipts_tuple(mapping):
    return tuple(sorted(mapping.values(), key=lambda receipt: receipt.tile_id))


class BudgetOracleTests(ProjectCase):
    def _in_memory_evidences(self, layout, grids, threshold):
        from evidence.budget_body import build_budget_body
        from intake.envelope import validate_envelope
        from survey.ingest import GridDraft
        from survey.revisions import FrozenRevision
        evidences = []
        for tile_id, rows in grids.items():
            tile = layout.tile(tile_id)
            grid = tuple(tuple(row) for row in rows)
            revision = FrozenRevision('0' * 32, layout.survey_id, tile_id, grid)
            body = build_budget_body(layout, revision, threshold)
            payload = json.dumps({'version': 2, 'packet_id': 'f' * 32,
                                  'survey_id': layout.survey_id,
                                  'layout_digest': '0' * 64, 'tile_id': tile_id,
                                  'revision_id': '0' * 32, 'body': body},
                                 sort_keys=True, separators=(',', ':'))
            evidences.append(parse_budget_evidence(layout, validate_envelope(payload)))
        return tuple(evidences)

    def test_closure_matches_full_grid_oracle_on_small_layouts(self):
        from itertools import product
        left, right = TileBounds('left', 0, 0, 2, 2), TileBounds('right', 2, 0, 2, 2)
        seams = tuple((PortRef('left', 1, y), PortRef('right', 2, y)) for y in (0, 1))
        probes = (('a', PortRef('left', 0, 0)), ('b', PortRef('left', 0, 1)),
                  ('c', PortRef('right', 3, 0)), ('d', PortRef('right', 3, 1)))
        layout = SurveyLayout('oracle-budget', (left, right), probes, seams)
        threshold = 5
        scores = (None, 0, 5, 7)
        pairs = (('a', 'd'), ('a', 'b'), ('c', 'd'), ('a', 'a'))
        for values in product(scores, repeat=8):
            grids = {'left': [list(values[0:2]), list(values[2:4])],
                     'right': [list(values[4:6]), list(values[6:8])]}
            evidences = self._in_memory_evidences(layout, grids, threshold)
            for probe_a, probe_b in pairs:
                observed = minimum_rescan(layout, evidences,
                                          layout.probe(probe_a), layout.probe(probe_b))
                expected = oracle_minimum(layout, grids, probe_a, probe_b, threshold)
                self.assertEqual(observed, expected, (grids, probe_a, probe_b))

    def test_full_stack_matches_oracle_on_sample_grids(self):
        layout, grids, correction = load_sample()
        register(self.station, layout)
        threshold = 5
        for name, chosen in (('initial', grids),
                             ('corrected', dict(grids, east=correction['rows']))):
            packets = {}
            for tile, rows in chosen.items():
                revision = freeze_grid(self.station, layout.survey_id, tile, rows)
                packets[tile] = issue_budget(self.station, revision, threshold)
            acks = {tile: accept(self.center, layout, packet.payload)
                    for tile, packet in packets.items()}
            accepted = [load_acceptance(self.center, ack.acceptance_id) for ack in acks.values()]
            evidences = tuple(parse_budget_evidence(layout, item.envelope) for item in accepted)
            observed = minimum_rescan(layout, evidences,
                                      layout.probe('top'), layout.probe('bottom'))
            expected = oracle_minimum(layout, chosen, 'top', 'bottom', threshold)
            self.assertEqual(observed, expected, name)

    def test_oracle_on_revision_and_seam_fixtures(self):
        for fixture_name, fixture, threshold, pairs in (
            ('revision', revision_pair(), 6, (('a', 'b'),)),
            ('seam', seam_pair(), 6, (('a', 'b'), ('edge', 'edge'), ('a', 'edge'))),
        ):
            layout, versions = fixture
            register(self.station, layout)
            for grids in versions:
                packets = {}
                for tile, rows in grids.items():
                    revision = freeze_grid(self.station, layout.survey_id, tile, rows)
                    packets[tile] = issue_budget(self.station, revision, threshold)
                acks = {tile: accept(self.center, layout, packet.payload)
                        for tile, packet in packets.items()}
                accepted = [load_acceptance(self.center, ack.acceptance_id) for ack in acks.values()]
                evidences = tuple(parse_budget_evidence(layout, item.envelope) for item in accepted)
                for probe_a, probe_b in pairs:
                    observed = minimum_rescan(layout, evidences,
                                              layout.probe(probe_a), layout.probe(probe_b))
                    expected = oracle_minimum(layout, grids, probe_a, probe_b, threshold)
                    self.assertEqual(observed, expected, fixture_name)
