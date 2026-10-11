"""Disposable UI fixture with original scanned pages and a mock model, never real data."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / 'qa'):
        raise ValueError('必须使用qa下全新合成验收目录')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output / 'synthetic.db'),
                      SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output / 'attachments'),
                      SUPPLY_CHAIN_BACKUP_DIR=str(output / 'backups'))
    sys.path.insert(0, str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from web_workbench.synthetic import seed
    from services import attachment_service, document_cashflow_service as cashflow
    from tests.test_document_understanding import interpretation
    from web_workbench.server import WorkbenchServer
    from PIL import Image, ImageDraw, ImageFont
    init_db()
    seed()
    for document in cashflow.list_documents(contract_id=1, project_id=1):
        cashflow.retire_document(document['id'], '合成模型资料专项验收')
    source = output / '合成付款合同.pdf'
    with Image.new('RGB', (1600, 1400), 'white') as image:
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 34)
        for index, line in enumerate(['合成资料，仅供界面验收，不是真实业务', '合同含税总价10万元',
                                      '2026年12月1日签约后预付30%',
                                      '验收后30天付至70%，质保完成付至100%']):
            draw.text((65, 100 + index * 160), line, fill='black', font=font)
        image.save(source, 'PDF', resolution=150)
    attachment_id = attachment_service.add_attachment('contract', 1, source, category='合同附件')
    result = interpretation('PDF第1页')
    client = Mock(model='合成视觉模型（未联网）', document_understanding=Mock(return_value=result))
    server = WorkbenchServer(0, read_only=False, demo=True)
    print(json.dumps({'url': server.origin, 'output': str(output), 'attachment_id': attachment_id}, ensure_ascii=False), flush=True)
    with patch('ai_engine.make_ai_client', return_value=client):
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
