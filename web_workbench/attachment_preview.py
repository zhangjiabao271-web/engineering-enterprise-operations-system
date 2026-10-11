"""Only inert image formats and PDF may be displayed inside the workbench."""
from pathlib import Path
import json
import subprocess
import sys


PREVIEW_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.pdf': 'application/pdf',
}


def preview_type(filename):
    return PREVIEW_TYPES.get(Path(filename).suffix.lower())


def preview_metadata(row):
    content_type = preview_type(row['original_name'])
    kind = ''
    if content_type:
        kind = 'pdf' if content_type == 'application/pdf' else 'image'
    return {**row, 'preview_kind': kind}


def resolve_attachment(query):
    from db.connection import db_read
    from services.attachment_service import attachment_path
    with db_read() as conn:
        row = conn.execute('SELECT * FROM business_attachments WHERE id=?', (int(query['id']),)).fetchone()
    if not row or (row['status'] != 'active' and query.get('history') != '1'):
        raise ValueError('附件不存在或已作废')
    file = attachment_path(row['file_path'])
    if not file.is_file():
        raise ValueError('原附件文件缺失，请核对本地资料')
    return row, file


def pdf_preview(file, page=None):
    """Isolate PDFium and bound rendering time, without a cache or source writes."""
    if file.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('PDF超过32MB，请下载原附件查看')
    worker = Path(__file__).with_name('pdf_preview_worker.py')
    arguments = [sys.executable, '-B', '-X', 'utf8', str(worker), str(file), str(0 if page is None else page)]
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as error:
        raise ValueError('PDF预览超时，请下载原附件查看，或换用较小文件') from error
    if result.returncode:
        try:
            message = json.loads(result.stdout)['error']
        except (ValueError, KeyError):
            message = 'PDF预览未完成，请核对文件是否损坏或加密'
        raise ValueError(message)
    return json.loads(result.stdout) if page is None else result.stdout
