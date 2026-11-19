"""Small scan fixtures and a check of the existing V1 evidence boundary."""
from contextlib import closing
import json
import sqlite3

from demo import initialize_center, initialize_station
from evidence.issue import issue
from intake.accept import accept
from reports.publish import review_issue
from reports.readback import retrieve
from survey.catalog import register
from survey.freeze import freeze_grid
from survey.layout import PortRef, SurveyLayout, TileBounds


def revision_pair():
    layout = SurveyLayout(
        "budget-line", (TileBounds("line", 0, 0, 4, 1),),
        (("a", PortRef("line", 0, 0)), ("b", PortRef("line", 3, 0))),
    )
    return layout, ({"line": [[7, 1, 7, 7]]}, {"line": [[7, 1, 1, 7]]})


def seam_pair():
    a = PortRef("west", 0, 0)
    mid = PortRef("west", 1, 0)
    edge = PortRef("west", 2, 0)
    b = PortRef("east", 3, 0)
    layout = SurveyLayout(
        "budget-seam", (TileBounds("west", 0, 0, 3, 1), TileBounds("east", 3, 0, 1, 1)),
        (("a", a), ("mid", mid), ("edge", edge), ("b", b)), ((edge, b),),
    )
    return layout, ({"west": [[7, 1, 1]], "east": [[7]]},
                    {"west": [[7, 1, None]], "east": [[7]]})


def check_v1():
    layout, grids_by_revision = revision_pair()
    with closing(sqlite3.connect(":memory:")) as station:
        initialize_station(station)
        register(station, layout)
        packets = tuple(issue(station, freeze_grid(station, layout.survey_id, "line", grids["line"]))
                        for grids in grids_by_revision)
    bodies = [json.loads(packet.payload)["body"] for packet in packets]
    if bodies[0] != bodies[1] or packets[0].revision_id == packets[1].revision_id:
        raise AssertionError("expected equal V1 evidence from distinct frozen revisions")

    actual = []
    with closing(sqlite3.connect(":memory:")) as center:
        initialize_center(center)
        for packet in packets:
            ack = accept(center, layout, packet.payload)
            report = review_issue(center, layout.survey_id, [ack.acceptance_id], "a", "b", 6)
            if retrieve(center, report.report_id) != report or report.parents != (ack,):
                raise AssertionError("V1 report must retain the selected revision")
            actual.append((report.decision.ceiling, report.decision.passed))
    if actual != [(1, False), (1, False)]:
        raise AssertionError(f"unexpected V1 decisions: {actual!r}")
    print(json.dumps({"v1_body": bodies[0], "v1_decisions": actual}, ensure_ascii=False))


if __name__ == "__main__":
    check_v1()
