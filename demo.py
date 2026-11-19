from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from evidence.issue import issue, issue_budget
from evidence.packets import initialize_packet_store
from intake.accept import accept
from intake.records import initialize_intake_store
from reports.archive import initialize_report_archive
from reports.publish import budget_issue, review_issue
from reports.readback import retrieve, retrieve_budget
from survey.catalog import initialize_catalog, register
from survey.freeze import freeze_grid
from survey.layout import PortRef, SurveyLayout, TileBounds
from survey.revisions import initialize_revision_store


def load_sample():
    sample_path = Path(__file__).resolve().parent / 'examples' / 'scan.json'
    data = json.loads(sample_path.read_text(encoding='utf-8'))
    return SurveyLayout.from_json(json.dumps(data['layout'])), data['grids'], data['correction']


def initialize_station(db):
    initialize_catalog(db)
    initialize_revision_store(db)
    initialize_packet_store(db)


def initialize_center(db):
    initialize_intake_store(db)
    initialize_report_archive(db)


def budget_fixture():
    a = PortRef('west', 0, 0)
    mid = PortRef('west', 1, 0)
    edge = PortRef('west', 2, 0)
    b = PortRef('east', 3, 0)
    layout = SurveyLayout(
        'budget-seam', (TileBounds('west', 0, 0, 3, 1), TileBounds('east', 3, 0, 1, 1)),
        (('a', a), ('mid', mid), ('edge', edge), ('b', b)), ((edge, b),),
    )
    return layout, ({'west': [[7, 1, 1]], 'east': [[7]]},
                    {'west': [[7, 1, None]], 'east': [[7]]})


def budget_demo(workspace):
    layout, versions = budget_fixture()
    threshold, budget = 6, 2
    station_file = Path(workspace) / 'budget_station.sqlite'
    center_file = Path(workspace) / 'budget_center.sqlite'
    with closing(sqlite3.connect(station_file)) as station:
        initialize_station(station)
        register(station, layout)
        packets = {}
        for tile, rows in versions[0].items():
            revision = freeze_grid(station, layout.survey_id, tile, rows)
            packets[tile] = issue_budget(station, revision, threshold)
    # station source stops before the center accepts the V2 packets
    with closing(sqlite3.connect(center_file)) as center:
        initialize_center(center)
        receipts = [accept(center, layout, packet.payload) for packet in packets.values()]
        ids = [receipt.acceptance_id for receipt in receipts]
        across = budget_issue(center, layout.survey_id, ids, 'a', 'b', budget)
        single = budget_issue(center, layout.survey_id, ids, 'edge', 'edge', 1)
    with closing(sqlite3.connect(station_file)) as station:
        initialize_station(station)
        correction = freeze_grid(station, layout.survey_id, 'west', versions[1]['west'])
        corrected_packet = issue_budget(station, correction, threshold)
    with closing(sqlite3.connect(center_file)) as center:
        corrected = accept(center, layout, corrected_packet.payload)
        chosen = [corrected.acceptance_id, receipts[1].acceptance_id]
        amended = budget_issue(center, layout.survey_id, chosen, 'a', 'b', budget)
    with closing(sqlite3.connect(center_file)) as center:
        held = retrieve_budget(center, across.report_id)
        held_single = retrieve_budget(center, single.report_id)
        held_amended = retrieve_budget(center, amended.report_id)
        assert (held, held_single, held_amended) == (across, single, amended)
        for record in (across, single, amended):
            print(f'预算报告 {record.report_id}  探针 {record.decision.probe_a}->{record.decision.probe_b}  '
                  f'门槛 {record.decision.threshold}  预算 {record.decision.budget}  '
                  f'最少补扫 {record.decision.minimum}  通过 {record.decision.passed}')
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'budget',
            'input': {'grids': versions, 'threshold': threshold, 'budget': budget},
            'expected': [[2, True], [1, True], [None, False]],
            'actual': [[across.decision.minimum, across.decision.passed],
                       [single.decision.minimum, single.decision.passed],
                       [amended.decision.minimum, amended.decision.passed]],
            'code_paths': ['evidence/issue.py', 'evidence/budget_body.py', 'analysis/budget_path.py',
                           'reports/publish.py', 'reports/readback.py'],
            'oracle': '西幅低于门槛的已测像元逐点计一次；末点缺测后出幅再入的通路被切断。'
        }, ensure_ascii=False))


def main():
    layout, grids, correction = load_sample()
    with TemporaryDirectory() as workspace:
        station_file = Path(workspace) / 'station.sqlite'
        center_file = Path(workspace) / 'center.sqlite'
        with closing(sqlite3.connect(station_file)) as station:
            initialize_station(station)
            register(station, layout)
            initial = {tile: issue(station, freeze_grid(station, layout.survey_id, tile, rows))
                       for tile, rows in grids.items()}
        with closing(sqlite3.connect(center_file)) as center:
            initialize_center(center)
            selected = {tile: accept(center, layout, packet.payload).acceptance_id
                        for tile, packet in initial.items()}
            old_report = review_issue(center, layout.survey_id, list(selected.values()), 'top', 'bottom', 6)
        with closing(sqlite3.connect(station_file)) as station:
            tile = correction['tile_id']
            corrected = issue(station, freeze_grid(station, layout.survey_id, tile, correction['rows']))
        with closing(sqlite3.connect(center_file)) as center:
            selected[tile] = accept(center, layout, corrected.payload).acceptance_id
            new_report = review_issue(center, layout.survey_id, list(selected.values()), 'top', 'bottom', 6)
        with closing(sqlite3.connect(center_file)) as center:
            old_copy = retrieve(center, old_report.report_id)
            new_copy = retrieve(center, new_report.report_id)
            assert old_copy == old_report and new_copy == new_report
            actual = [old_report.decision.ceiling, new_report.decision.ceiling]
            downstream = [old_copy.handover.action, new_copy.handover.action]
            assert actual == [5, 3] and downstream == ['补测', '补测']
            assert next(parent.packet_id for parent in old_copy.parents if parent.tile_id == tile) == initial[tile].packet_id
            assert next(parent.packet_id for parent in new_copy.parents if parent.tile_id == tile) == corrected.packet_id
            for record in (old_copy, new_copy):
                print(f'报告 {record.report_id}  上限 {record.decision.ceiling}  '
                      f'请求门槛 {record.decision.threshold}  {record.handover.action}')
            print('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': 'historical',
                'input': {'grids': grids, 'correction': correction, 'requested_threshold': 6},
                'expected': [5, 3], 'actual': actual,
                'downstream_expected': ['补测', '补测'], 'downstream_actual': downstream,
                'code_paths': ['evidence/issue.py', 'analysis/compose.py', 'reports/archive.py',
                               'reports/readback.py'],
                'oracle': '东西幅上、下排均为 7 分，连接两排的右幅中间行最大值先为 5 后为 3。'
            }, ensure_ascii=False))
        budget_demo(workspace)


if __name__ == '__main__':
    main()
