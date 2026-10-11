"""Bounded, offline PDF rasterization in a disposable process."""
import io
import json
import math
import sys


def preview(file, number):
    from pypdf import PdfReader
    reader = PdfReader(file)
    if reader.is_encrypted:
        raise ValueError('此PDF已加密，请下载原附件并使用密码查看')
    pages = len(reader.pages)
    if pages < 1:
        raise ValueError('PDF没有可预览的页面')
    if number != 0 and not 1 <= number <= pages:
        raise ValueError('PDF页码超出范围')
    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise ValueError('本机PDF预览组件未安装，请安装项目 requirements.txt') from error
    with pdfium.PdfDocument(file) as document:
        if number == 0:
            sizes = []
            for index in range(pages):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        raise ValueError('PDF页面尺寸无效')
                    sizes.append({'width': width, 'height': height})
                finally:
                    page.close()
            return json.dumps({'pages': pages, 'page_sizes': sizes}).encode('utf-8')
        page = document[number - 1]
        try:
            width, height = page.get_size()
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise ValueError('PDF页面尺寸无效')
            bitmap = page.render(scale=min(2.5, 2400 / max(width, height)))
            try:
                picture = bitmap.to_pil()
                try:
                    output = io.BytesIO()
                    picture.save(output, format='PNG')
                    return output.getvalue()
                finally:
                    picture.close()
            finally:
                bitmap.close()
        finally:
            page.close()


if __name__ == '__main__':
    try:
        raw = preview(sys.argv[1], int(sys.argv[2]))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else 'PDF预览未完成，请核对文件是否损坏或加密'
        sys.stdout.buffer.write(json.dumps({'error': message}, ensure_ascii=False).encode('utf-8'))
        sys.exit(1)
    sys.stdout.buffer.write(raw)
