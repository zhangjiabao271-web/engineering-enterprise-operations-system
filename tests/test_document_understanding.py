"""Synthetic documents and mock responses only; no credentials or real ledger."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_client import AIClient, AIError
from services.document_cashflow_extract import extract_rows, timeline, validate_fragments
from services.document_cashflow_semantics import normalize_understanding
from services.document_understanding_service import understand_document
from tests import test_document_cashflow as lifecycle


def interpretation(locator='第1行'):
    return {'kind': '合同正文', 'summary': '先垫付材料，签约取得30%预付款，验收后付至70%，尾款待完成质保。',
            'covered_sources': [locator], 'unreadable_sources': [],
            'bases': [{'id': 'contract', 'label': '含税合同总价', 'amount': '100000.00',
                       'evidence': [{'locator': locator, 'quote': '合同含税总价10万元'}]}],
            'nodes': [node('预付款', '30', 'incremental', locator),
                      node('验收款', '70', 'cumulative', locator),
                      node('尾款', '100', 'cumulative', locator)],
            'risks': [{'text': '验收与质保完成日未确定，不能预计尾款到账日。',
                       'evidence': [{'locator': locator, 'quote': '验收后30天付至70%，质保完成付至100%'}]}]}


def node(title, percentage, mode, locator):
    quote = {'预付款': '2026年12月1日签约后预付30%', '验收款': '验收后30天付至70%',
             '尾款': '质保完成付至100%'}[title]
    return {'title': title, 'direction': 'in', 'scope': 'current',
            'amount': {'type': 'percentage', 'value': percentage, 'base_id': 'contract', 'percentage_mode': mode},
            'due_date': '2026-12-01' if title == '预付款' else None,
            'timing': '签约后' if title == '预付款' else quote,
            'condition': '签约完成' if title == '预付款' else '需验收/质保完成', 'counterparty': '合成客户',
            'issues': [], 'evidence': [{'locator': locator, 'quote': quote}]}


RAW_TEXT = '合同含税总价10万元。2026年12月1日签约后预付30%，验收后30天付至70%，质保完成付至100%。'


def packet(result=None, *, vision=False):
    return {'schema': 1, 'model': 'synthetic-vision', 'scope': {'project_scope': True, 'multi_project_contract': False},
            'source_mode': 'vision' if vision else 'text', 'result': result or interpretation()}


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.fragments = [{'locator': '第1行', 'text': RAW_TEXT}]

    def test_business_meaning_and_cumulative_stages(self):
        result = normalize_understanding(packet(), self.fragments)
        self.assertEqual([row['amount_minor'] for row in result['nodes']], [3000000, 4000000, 3000000])
        self.assertEqual(sum(row['amount_minor'] for row in result['nodes']), 10000000)
        self.assertIsNone(result['nodes'][1]['due_date'])
        self.assertEqual(result['nodes'][1]['timing'], '验收后30天付至70%')
        self.assertFalse(result['nodes'][0]['issues'])
        source = {'id': 1, 'name': '合成资料', 'version': 1, 'sha256': 'synthetic', 'kind': '合同正文',
                  'fragments_json': json.dumps([{**self.fragments[0], 'understanding': packet()}])}
        rows = extract_rows([source])
        self.assertEqual(len(rows), 3)
        self.assertEqual(timeline(rows)['schedule'][0]['in_minor'], 3000000)
        self.assertEqual(len(timeline(rows)['excluded_keys']), 2)

    def test_no_hallucinated_dates_pages_quotes_or_missing_pages(self):
        changes = [lambda r: r['nodes'][1].update(due_date='2027-01-01'),
                   lambda r: r['nodes'][0]['evidence'][0].update(locator='第99页'),
                   lambda r: r['nodes'][0]['evidence'][0].update(quote='根本不存在的条款'),
                   lambda r: r.update(covered_sources=[]),
                   lambda r: r.update(unreadable_sources=['第1行'])]
        for change in changes:
            result = interpretation()
            change(result)
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_understanding(packet(result), self.fragments)

    def test_totals_ambiguity_and_overpayment_cannot_enter_timeline(self):
        result = interpretation()
        result['nodes'][1]['scope'] = 'whole_contract'
        normalized = normalize_understanding(packet(result), self.fragments)
        self.assertTrue(any('当前项目' in issue for issue in normalized['nodes'][1]['issues']))
        result = interpretation()
        result['nodes'][2]['amount']['percentage_mode'] = 'incremental'
        normalized = normalize_understanding(packet(result), self.fragments)
        self.assertTrue(all(any('异常' in issue for issue in node['issues']) for node in normalized['nodes']))
        result = interpretation()
        result['bases'][0]['amount'] = '200000'
        normalized = normalize_understanding(packet(result), self.fragments)
        self.assertTrue(all(any('基数' in issue for issue in row['issues']) for row in normalized['nodes']))
        result = interpretation()
        result['nodes'][0]['amount'] = {'type': 'fixed', 'value': '3000', 'base_id': None}
        normalized = normalize_understanding(packet(result), self.fragments)
        self.assertIsNone(normalized['nodes'][0]['amount_minor'])

    def test_strict_packet_persistence_and_duplicate_safety(self):
        fragments = [{**self.fragments[0], 'understanding': packet()}]
        self.assertIn('understanding', validate_fragments(fragments)[0])
        with self.assertRaises(ValueError):
            validate_fragments(fragments * 2)
        result = interpretation()
        result['nodes'][1] = copy.deepcopy(result['nodes'][0])
        result['nodes'][1]['issues'] = ['归属待核对']
        doc = {'id': 1, 'name': '合成', 'version': 1, 'sha256': 'synthetic', 'kind': '合同正文',
               'fragments_json': json.dumps([{**self.fragments[0], 'understanding': packet(result)}])}
        rows = extract_rows([doc])
        first = next(row for row in rows if row['title'] == '预付款')
        self.assertIn('归属待核对', first['issues'])
        self.assertIn(first['key'], timeline(rows)['excluded_keys'])


class OriginalPageTests(unittest.TestCase):
    def client(self, result):
        return Mock(model='synthetic-vision', document_understanding=Mock(return_value=result))

    def test_scanned_pdf_all_pages_go_directly_to_one_model_request(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '合成扫描.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=400)
            writer.add_blank_page(width=300, height=400)
            writer.write(path)
            result = interpretation('PDF第1页')
            result['covered_sources'].append('PDF第2页')
            client = self.client(result)
            with patch('services.document_ocr_service.recognize_pdf_pages', side_effect=AssertionError('不应调用OCR')):
                document = understand_document(path, context={'project': {'name': '合成工程'}}, client=client)
            client.document_understanding.assert_called_once()
            content = client.document_understanding.call_args.args[0][1]['content']
            images = [part['image_url']['url'] for part in content if part['type'] == 'image_url']
            self.assertEqual(len(images), 2)
            self.assertTrue(all(image.startswith('data:image/jpeg;base64,') for image in images))
            self.assertEqual(document['recognition']['ocr_pages'], [])
            self.assertEqual(document['recognition']['page_count'], 2)
            self.assertEqual(document['understanding']['nodes'][1]['amount_minor'], 4000000)

    def test_text_and_images_share_business_semantics(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / '合成.txt'
            path.write_text(RAW_TEXT, encoding='utf-8')
            client = self.client(interpretation())
            result = understand_document(path, context={'project': {'name': '合成'}}, client=client)
            self.assertEqual(result['recognition']['mode'], 'text')
            self.assertIn('我方视角', client.document_understanding.call_args.args[0][0]['content'])
            image_path = root / '合成.png'
            with Image.new('RGB', (600, 800), 'white') as image:
                image.save(image_path)
            result = understand_document(image_path, context={}, client=self.client(interpretation('图片第1页')))
            self.assertEqual(result['recognition']['mode'], 'vision')

    def test_limits_or_model_failure_never_fallback_or_retry(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '合成长.pdf'
            writer = PdfWriter()
            for _ in range(21):
                writer.add_blank_page(width=100, height=100)
            writer.write(path)
            client = self.client(interpretation())
            with self.assertRaisesRegex(ValueError, '不会跳过'):
                understand_document(path, context={}, client=client)
            client.document_understanding.assert_not_called()
            path = Path(temp) / '合成.txt'
            path.write_text(RAW_TEXT, encoding='utf-8')
            client.document_understanding.side_effect = AIError('不支持视觉', code='invalid_request')
            with self.assertRaisesRegex(ValueError, '不会用OCR'):
                understand_document(path, context={}, client=client)
            client.document_understanding.assert_called_once()


class ClientTests(unittest.TestCase):
    def test_complete_json_only_and_no_auto_retry(self):
        for choice in ({'finish_reason': 'length', 'message': {'content': '{}'}},
                       {'finish_reason': 'stop', 'message': {'reasoning_content': '{}'}},
                       {'finish_reason': 'stop', 'message': {'content': '{broken'}},
                       {'finish_reason': 'stop', 'message': {'content': '[]'}}):
            client = AIClient('synthetic-key')
            with patch.object(client, '_request_json', return_value={'choices': [choice]}) as request:
                with self.assertRaises(AIError):
                    client.document_understanding([])
                request.assert_called_once()
        client = AIClient('synthetic-key', model='synthetic-vision')
        with patch.object(client, '_request_json', return_value={'choices': [
                {'finish_reason': 'stop', 'message': {'content': json.dumps(interpretation())}}]}) as request:
            self.assertEqual(client.document_understanding([])['kind'], '合同正文')
            self.assertEqual(request.call_args.args[2]['response_format'], {'type': 'json_object'})
            self.assertEqual(request.call_args.args[2]['model'], 'synthetic-vision')


class SemanticLifecycleTests(unittest.TestCase):
    setUpClass = classmethod(lifecycle.CashflowLifecycleTests.setUpClass.__func__)
    tearDownClass = classmethod(lifecycle.CashflowLifecycleTests.tearDownClass.__func__)
    setUp = lifecycle.CashflowLifecycleTests.setUp
    count = lifecycle.CashflowLifecycleTests.count

    def save_understanding(self):
        semantic = packet()
        semantic['scope_fingerprint'] = self.service.understanding_context(**self.scope)[1]
        return self.service.save_document({'name': '合成语义合同', 'kind': '合同正文',
            'fragments': [{'locator': '第1行', 'text': RAW_TEXT, 'understanding': semantic}]}, **self.scope)

    def test_preview_data_analysis_and_confirmation_do_not_invent_business_facts(self):
        before = {table: self.count(table) for table in ('settlements', 'sales_invoices', 'receipts', 'cost_entries', 'fund_transactions')}
        self.save_understanding()
        identity = self.service.create_analysis(**self.scope)
        result = self.service.get_analysis(identity)
        self.assertEqual(result['payload']['interpretations'][0]['summary'], interpretation()['summary'])
        self.assertEqual(len(result['payload']['rows']), 3)
        known = next(row for row in result['payload']['rows'] if row['due_date'])
        unknown = next(row for row in result['payload']['rows'] if not row['due_date'])
        with self.assertRaises(ValueError):
            self.service.confirm_analysis(identity, [unknown['key']], acknowledged=True, reason='合成测试')
        plans = self.service.confirm_analysis(identity, [known['key']], acknowledged=True, reason='对照原文核对')
        self.assertEqual(len(plans), 1)
        self.assertEqual(before, {table: self.count(table) for table in before})

    def test_changed_scope_after_reading_rejects_save_and_invalidates_analysis(self):
        from db.connection import db_transaction
        self.save_understanding()
        identity = self.service.create_analysis(**self.scope)
        with db_transaction() as conn:
            conn.execute('UPDATE projects SET name=? WHERE id=?', ('合成已改名', self.project_id))
        self.assertTrue(self.service.get_analysis(identity)['stale_reason'])
        with self.assertRaises(ValueError):
            self.service.create_analysis(**self.scope)

    def test_report_preserves_interpretation_timing_and_evidence(self):
        from services.document_cashflow_report import export_html, report_text
        self.save_understanding()
        identity = self.service.create_analysis(**self.scope)
        text = report_text(self.service.get_analysis(identity))
        self.assertIn('先垫付材料', text)
        self.assertIn('验收后30天', text)
        self.assertIn('系统复算', text)
        path = Path(self.temp.name) / '合成语义报告.html'
        export_html(identity, path)
        html = path.read_text(encoding='utf-8')
        self.assertIn('资料业务理解', html)
        self.assertIn('先垫付材料', html)
        self.assertIn('验收后30天', html)


if __name__ == '__main__':
    unittest.main()
