"""Opt-in model planning acceptance using only disposable, synthetic facts."""

import argparse
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_client import AIError
from ai_engine import make_ai_client
from services.ai_query_planner import query_turn
from services import ai_operating_query_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send generic sample questions and tool descriptions to the configured model')
    if not parser.parse_args().live:
        parser.error('Live planning requires --live; no ledger rows or attachments are sent.')
    client = make_ai_client()
    client.timeout = 25
    with tempfile.TemporaryDirectory(prefix='ai_intents_') as temporary:
        database = Path(temporary) / 'facts.db'
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.executescript('''
                CREATE TABLE projects(id INTEGER,name TEXT,customer_partner_id INTEGER);
                CREATE TABLE contracts(id INTEGER,customer_partner_id INTEGER);
                CREATE TABLE business_partners(id INTEGER,legal_name TEXT);
                CREATE TABLE settlements(id INTEGER,settlement_date TEXT,settlement_no TEXT,
                    project_id INTEGER,contract_id INTEGER,amount_minor INTEGER,status TEXT);
                CREATE TABLE receipts(id INTEGER,receipt_date TEXT,receipt_no TEXT,status TEXT,amount_minor INTEGER);
                CREATE TABLE receipt_allocations(id INTEGER,receipt_id INTEGER,project_id INTEGER,
                    contract_id INTEGER,allocated_amount_minor INTEGER,status TEXT);
                CREATE TABLE historical_receivable_projects(id INTEGER,customer_id INTEGER,name TEXT);
                CREATE TABLE historical_receipts(id INTEGER,project_id INTEGER,receipt_date TEXT,amount_minor INTEGER,status TEXT);
                CREATE TABLE workers(id INTEGER,name TEXT);
                CREATE TABLE work_logs(id INTEGER,worker_id INTEGER,project_id INTEGER,
                    work_date TEXT,construction_site TEXT,work_days REAL,is_overtime INTEGER,
                    amount_minor INTEGER,amount REAL,status TEXT);
                INSERT INTO projects VALUES(1,'演示项目甲',1),(2,'演示项目乙',2);
                INSERT INTO business_partners VALUES(1,'演示客户甲'),(2,'演示客户乙');
                INSERT INTO workers VALUES(1,'演示工人甲'),(2,'演示工人乙');
            ''')
            today = date.today().isoformat()
            conn.executemany('INSERT INTO settlements VALUES(?,?,?, ?,NULL,?,\'active\')',
                             [(1, today, 'S1', 1, 100000), (2, today, 'S2', 2, 50000)])
            conn.executemany("INSERT INTO receipts VALUES(?,? ,?,'active',?)",
                             [(1, today, 'R1', 20000), (2, today, 'R2', 40000)])
            conn.executemany("INSERT INTO receipt_allocations VALUES(?,?,?,NULL,?,'active')",
                             [(1, 1, 1, 20000), (2, 2, 2, 40000)])
            conn.execute("INSERT INTO historical_receivable_projects VALUES(1,2,'演示旧账')")
            conn.execute("INSERT INTO historical_receipts VALUES(1,1,?,150000,'active')", (today,))
            conn.executemany("INSERT INTO work_logs VALUES(?,?,1,?,'演示工地',?,0,10000,100,'active')",
                             [(1, 1, today, 1), (2, 2, today, 0.5)])
        context = {}
        cases = [('今年业务做得最多的是哪个客户？', 'income', 'leader', '演示客户甲'),
                 ('那把所有客户的收入排名列出来', 'income', 'ranking', '| 客户 |'),
                 ('那回款最多的客户是谁？', 'collections', 'leader', '演示客户乙'),
                 ('今年工天最少的是哪位工人？', 'labor', 'leader', '演示工人乙')]
        for index, (question, dataset, mode, expected) in enumerate(cases, 1):
            if index == 4:
                context = {}
            turn = query_turn(question, client, conversation_context=context, db_path=database)
            context = turn['context_updates']
            queries = (context.get('query_plan') or {}).get('queries', [])
            assert len(queries) == 1, f'case {index}: no unique completed query'
            assert queries[0]['dataset'] == dataset and queries[0].get('result_mode') == mode, f'case {index}: incorrect intent'
            assert expected in turn['answer'], f'case {index}: incorrect local result'
            print(json.dumps({'case': index, 'dataset': dataset, 'result_mode': mode, 'status': 'passed'}), flush=True)

        turn = query_turn('全公司今年总共收到多少回款？包括历史旧账。', client, db_path=database)
        queries = (turn['context_updates'].get('query_plan') or {}).get('queries', [])
        assert len(queries) == 1 and queries[0]['dataset'] == 'collections', 'Actual collections must include history once'
        assert '¥2,100.00' in turn['answer'], 'Historical collection was omitted or counted as income'
        print(json.dumps({'case': 'collections_with_history', 'status': 'passed'}), flush=True)

        turn = query_turn('查看历史旧账回款明细，按金额从小到大排列。', client, db_path=database)
        queries = (turn['context_updates'].get('query_plan') or {}).get('queries', [])
        assert len(queries) == 1 and queries[0]['dataset'] == 'historical_receipts', 'Historical records must use their own registry'
        assert queries[0].get('result_mode') == 'details' and queries[0].get('order_by') == 'amount_minor', 'Detail sorting not planned'
        assert queries[0].get('descending') is False, 'Detail direction not planned'
        assert '¥1,500.00' in turn['answer'], 'Historical detail not returned'
        print(json.dumps({'case': 'historical_detail_sort', 'status': 'passed'}), flush=True)

        # These synthetic service rows isolate model selection from financial
        # matching, which has its own real-service database regression tests.
        invoices = [{'project_id': 1, 'project_name': '演示项目甲', 'invoice_date': today,
                     'invoice_no': 'DEMO-I1', 'amount_minor': 100000, 'received_minor': 60000,
                     'unreceived_minor': 40000, 'collection_status': '部分回款'}]
        receipts = [{'project_id': 2, 'project_name': '演示项目乙', 'receipt_date': today,
                     'receipt_no': 'DEMO-R2', 'amount_minor': 60000, 'invoice_matched_minor': 40000,
                     'invoice_unmatched_minor': 20000, 'invoice_no': 'DEMO-I2'}]
        with patch.object(ai_operating_query_service.finance_service, 'list_invoices', return_value=invoices), patch.object(
            ai_operating_query_service.finance_service, 'list_receipts', return_value=receipts
        ):
            for index, (question, expected_tools, expected_amount) in enumerate([
                ('那哪些发票还没有结清，剩余余额多少？', {'invoice_balance'}, '¥400.00'),
                ('再看看哪些回款在等后续发票来抵扣？', {'receipt_matching'}, '¥200.00'),
                ('把当前发票余额和回款待抵发票一起看看', {'invoice_balance', 'receipt_matching'}, '¥400.00'),
            ], 5):
                turn = query_turn(question, client, conversation_context=context, db_path=database)
                context = turn['context_updates']
                queries = (context.get('query_plan') or {}).get('queries', [])
                assert {query['dataset'] for query in queries} == expected_tools, f'case {index}: existing capability not selected'
                assert expected_amount in turn['answer'], f'case {index}: incorrect local service answer'
                print(json.dumps({'case': index, 'tools': sorted(expected_tools), 'status': 'passed'}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except AIError as error:
        print(json.dumps({'status': 'not_verified', 'code': error.code}), flush=True)
        sys.exit(1)
