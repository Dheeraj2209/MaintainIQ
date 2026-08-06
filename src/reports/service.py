"""Report generation service (Phase 6): build summary, render, persist, fetch."""
from __future__ import annotations

import json

from src.reports import generators


def generate(conn, *, report_type, scope, format, period_start=None,
             period_end=None, generated_by=None) -> dict:
    if report_type not in generators.REPORT_TYPES:
        raise ValueError(f"unknown report_type: {report_type}")
    if format not in generators.FORMATS:
        raise ValueError(f"unknown format: {format}")

    summary = generators.build_summary(
        conn, report_type=report_type, scope=scope,
        period_start=period_start, period_end=period_end,
    )
    content = generators.render(format, summary)
    summary_json = json.dumps(summary)

    cur = conn.execute(
        """INSERT INTO reports
               (report_type, scope, format, period_start, period_end,
                generated_by, content, summary_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (report_type, scope, format, period_start, period_end,
         generated_by, content, summary_json),
    )
    conn.commit()
    return get_report(conn, cur.lastrowid)


def get_report(conn, report_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row is not None else None


def list_reports(conn, *, report_types=None, scope=None, limit=100) -> list[dict]:
    clauses = []
    params: list = []
    if report_types:
        placeholders = ",".join("?" for _ in report_types)
        clauses.append(f"report_type IN ({placeholders})")
        params.extend(report_types)
    if scope is not None:
        clauses.append("scope = ?")
        params.append(scope)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""SELECT id, report_type, scope, format, period_start, period_end,
                   generated_at, generated_by, summary_json
            FROM reports{where}
            ORDER BY id DESC LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]
