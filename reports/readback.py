import sqlite3

from reports.archive import BudgetReport, ReportRecord, load_budget_report, load_report


def retrieve(db: sqlite3.Connection, report_id: int) -> ReportRecord | None:
    return load_report(db, report_id)


def retrieve_budget(db: sqlite3.Connection, report_id: int) -> BudgetReport | None:
    return load_budget_report(db, report_id)
