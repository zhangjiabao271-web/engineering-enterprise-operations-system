"""One period and one snapshot for the browser cost ledger and its totals."""
from datetime import date

from db.connection import db_read, use_read_snapshot
from services import cost_service, project_service


def ledger_dashboard(project_id=None, month=None):
    period = month or 'all'
    if period != 'all':
        try:
            start = date.fromisoformat(str(period) + '-01')
            if start.strftime('%Y-%m') != period:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('成本月份须为有效的 YYYY-MM，或选择全部期间') from None
    with db_read() as conn, use_read_snapshot(conn):
        ledger = cost_service.list_cost_ledger(project_id)
        project_names = {row['id']: row['name'] for row in project_service.list_projects()}
        months = sorted({row['business_date'][:7] for row in ledger if row['business_date']}, reverse=True)
        if period != 'all':
            ledger = [row for row in ledger if str(row['business_date'] or '').startswith(period + '-')]
        for row in ledger:
            row['allocation_project_names'] = row.get('allocation_project_names') or project_names.get(row.get('project_id'), '待归集')
        summary = {'total_minor': sum(row['amount_minor'] for row in ledger)}
        for source in ('purchase', 'labor', 'manual'):
            rows = [row for row in ledger if row['source_type'] == source]
            summary[source + '_minor'] = sum(row['amount_minor'] for row in rows)
            summary[source + '_count'] = len(rows)
        return {'summary': summary, 'rows': ledger, 'months': months, 'period': period,
                'period_label': '全部期间（全周期）' if period == 'all' else period + ' 月'}
