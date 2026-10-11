"""Add synthetic-only preview samples to a newly created demo ledger."""
import json
import os
import sqlite3
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1]).resolve()
    if not output.is_relative_to(root / 'qa'):
        raise ValueError('Preview fixtures must stay inside qa')
    database = output / 'supplier_data.db'
    with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as conn:
        name = conn.execute('SELECT name FROM projects WHERE id=1').fetchone()[0]
        if not name.startswith('合成演示'):
            raise ValueError('Only a synthetic demo ledger is allowed')
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(database),
                      SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output / 'attachments'))
    sys.path.insert(0, str(root))
    if '--serve' in sys.argv:
        from web_workbench.server import WorkbenchServer
        server = WorkbenchServer(23339, read_only=False, demo=True)
        print(server.origin, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    from PIL import Image, ImageDraw, ImageFont
    from services.attachment_service import add_attachment
    from services.web_workbench_operations import database_fingerprint
    from db.connection import db_read

    if '--check' in sys.argv:
        fixture = json.loads((output / 'fixture.json').read_text(encoding='utf-8'))
        with db_read() as conn:
            result = {'ledger_unchanged': database_fingerprint(conn) == fixture['baseline'],
                      'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0],
                      'foreign_key_errors': len(conn.execute('PRAGMA foreign_key_check').fetchall())}
        if result != {'ledger_unchanged': True, 'integrity': 'ok', 'foreign_key_errors': 0}:
            raise AssertionError(result)
        print(json.dumps(result))
        return
    if (output / 'fixture.json').exists():
        raise ValueError('Preview fixtures already exist; refusing to add them twice')

    title_font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 36)
    body_font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 22)
    pictures = []
    for index in range(12):
        size = (1190, 1684) if index % 3 != 2 else (1684, 1190)
        picture = Image.new('RGB', size, 'white')
        draw = ImageDraw.Draw(picture)
        draw.rectangle((60, 60, size[0] - 60, 230), fill='#eaf3ff')
        draw.text((90, 90), f'CONTINUOUS PDF PREVIEW - PAGE {index + 1:02}',
                  fill='#0066cc', font=title_font)
        draw.text((90, 150), 'Synthetic test document - no real business data',
                  fill='#63636b', font=body_font)
        for row in range(8):
            draw.text((90, 300 + row * 72), f'Page {index + 1:02} / Reading line {row + 1:02}',
                      fill='#1d1d1f', font=body_font)
            draw.line((90, 345 + row * 72, size[0] - 90, 345 + row * 72), fill='#dedee5', width=2)
        pictures.append(picture)
    source = output / 'continuous-12-pages.pdf'
    pictures[0].save(source, 'PDF', resolution=144, save_all=True, append_images=pictures[1:])
    image_source = output / 'preview-image.png'
    pictures[0].save(image_source)
    for picture in pictures:
        picture.close()
    ids = {'pdf': add_attachment('contract', 1, source),
           'image': add_attachment('contract', 1, image_source),
           'purchase_pdf': add_attachment('purchase', 1, source)}
    with db_read() as conn:
        baseline = database_fingerprint(conn)
    (output / 'fixture.json').write_text(json.dumps({'ids': ids, 'baseline': baseline}, indent=2), encoding='utf-8')
    print(json.dumps(ids))


if __name__ == '__main__':
    main()
