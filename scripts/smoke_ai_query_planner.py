"""Opt-in live planner check; no credentials, facts or ledger rows are printed."""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_engine import make_ai_client
from services.ai_query_planner import query_turn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send sample questions and tool descriptions to the configured model')
    args = parser.parse_args()
    if not args.live:
        parser.error('Live calls require --live and authorization for external query planning.')
    client = make_ai_client()
    client.timeout = 35
    context = {}
    questions = [
        '把示例工人甲今年每个月的工天列出来，区分加班',
        '那上个月呢？',
        '全公司今年每个月采购整单金额，以及按供应商的采购金额占比',
    ]
    for index, question in enumerate(questions, 1):
        turn = query_turn(question, client, conversation_context=context)
        for key, value in turn['context_updates'].items():
            if value is None:
                context.pop(key, None)
            else:
                context[key] = value
        plan = context.get('query_plan') or {}
        queries = plan.get('queries') or []
        assert turn['sources'], (index, turn['intent'])
        if index == 1:
            assert queries[0]['dataset'] == 'labor'
            assert queries[0].get('group_by') == ['month']
            assert 'worker' in queries[0].get('filters', {})
            assert {'days', 'overtime_days'} <= set(queries[0]['metrics'])
        elif index == 2:
            assert queries[0]['dataset'] == 'labor'
            assert 'worker' in queries[0].get('filters', {})
            last_month_end = date.today().replace(day=1) - timedelta(days=1)
            assert queries[0]['start_date'] == last_month_end.replace(day=1).isoformat()
            assert queries[0]['end_date'] == last_month_end.isoformat()
        else:
            assert len(queries) == 2
            assert all(q['dataset'] == 'purchases' and not q.get('filters', {}).get('worker') for q in queries)
        print(json.dumps({'case': index, 'sources': len(turn['sources']), 'status': 'passed'}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
