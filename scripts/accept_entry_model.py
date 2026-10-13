"""Test the configured model on fictional text only; no ledger rows are sent."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    output = parser.parse_args().output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'qa'):
        raise ValueError('请选择qa下新的结果文件')
    output.parent.mkdir(parents=True, exist_ok=True)
    from ai_engine import make_ai_client
    from services.assistant_entry_service import extract
    checks = []
    try:
        client = make_ai_client()
        question = ('以下全部是虚构验收业务，日期2026-10-14。'
                    '合成甲工程买烟470元，已付，归业务招待费；'
                    '同项目在合成乙五金店买油漆12桶，每桶含税20元，税率1%，现金已付有发票；'
                    '合成丙工程银行转账收到1000元；'
                    '合成张工和合成李工在合成甲工程的东侧车间各做半天，普通出勤不加班。')
        plan = extract(question, client=client)
        by_module = {row['module']:row['data'] for row in plan['records']}
        assert set(by_module) == {'costs','purchases','receipts','worklogs'}
        assert all(row['date']=='2026-10-14' for row in by_module.values())
        assert float(by_module['costs']['amount']) == 470
        assert float(by_module['receipts']['amount']) == 1000
        item = by_module['purchases']['items'][0]
        assert float(item['quantity']) == 12 and float(item['unit_price']) == 20
        assert item['price_basis'] == 'inclusive' and float(item['tax_percent']) == 1
        assert set(by_module['worklogs']['workers']) == {'合成张工','合成李工'}
        assert float(by_module['worklogs']['days']) == 0.5
        checks.append('四类混合描述的日期、金额、税率、数量与半天工人名单正确提取')
        missing = extract('2026-10-14合成甲工程在合成乙五金店买油漆共240元含税，现金已付有发票，税率不知道。',client=client)
        assert missing['records'][0]['data']['items'][0].get('tax_percent') in (None,'')
        assert missing['questions']
        checks.append('缺税率保留待核对，不猜测历史税率')
        unsupported = extract('2026-10-14合成甲工程买烟470元已付，另外工具300元均摊所有未完工项目。',client=client)
        assert unsupported['questions']
        checks.append('未支持的多项目均摊要求明确提示，不默默省略')
        result = {'status':'passed','fictional_input_only':True,'formal_ledger_sent':False,'checks':checks}
    except Exception as error:
        # Never echo client configuration, endpoints, credentials or raw provider errors.
        result = {'status':'not_passed','fictional_input_only':True,'formal_ledger_sent':False,
                  'checks':checks,'error_type':type(error).__name__}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
