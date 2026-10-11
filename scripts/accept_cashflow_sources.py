"""Fresh synthetic scanner fixture for manual browser acceptance."""

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / 'qa'):
        raise ValueError('必须指定qa下全新合成验收目录')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output / 'synthetic.db'),
                      SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output / 'attachments'),
                      SUPPLY_CHAIN_BACKUP_DIR=str(output / 'backups'))
    sys.path.insert(0, str(root))
    from db.schema import init_db
    from web_workbench.synthetic import seed
    from services import attachment_service, document_cashflow_service as cashflow
    from PIL import Image, ImageDraw, ImageFont
    init_db()
    seed()
    # Keep the scan as the only source in the chosen exact scope.
    for document in cashflow.list_documents(contract_id=1, project_id=1):
        cashflow.retire_document(document['id'], '合成扫描附件专项验收')
    image = Image.new('RGB', (1600, 1000), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 34)
    lines = ['合成扫描合同（仅验收，不是真实业务）', '第一条：合成工程资料',
             '收款 合成尾款；金额：100元；日期：2026-12-01；条件：验收完成',
             '识别结果须对照原件核对，不自动生成账目。']
    for index, text in enumerate(lines):
        draw.text((60, 100 + index * 150), text, font=font, fill='black')
    source = output / '合成扫描合同.pdf'
    image.save(source, 'PDF', resolution=150)
    image.close()
    attachment_id = attachment_service.add_attachment('contract', 1, source, category='合同附件')
    from web_workbench.server import WorkbenchServer
    server = WorkbenchServer(0, read_only=False, demo=True)
    print(json.dumps({'url': server.origin, 'output': str(output),
                      'attachment_id': attachment_id}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
