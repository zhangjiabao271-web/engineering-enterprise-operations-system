"""Optional, offline Chinese OCR for bounded scanned PDF pages."""

import threading

MAX_OCR_PAGES = 20
_ocr_lock = threading.Lock()
_engine = None


def recognize_pdf_pages(raw, page_numbers):
    if len(page_numbers) > MAX_OCR_PAGES:
        raise ValueError('扫描资料一次最多识别20页，请拆分PDF后读取')
    try:
        import pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return {}, ['本机未安装扫描识别依赖，请安装 requirements-ocr.txt 或使用人工摘录']
    if not _ocr_lock.acquire(blocking=False):
        raise ValueError('本机正在识别另一份扫描资料，请稍后再读取')
    global _engine
    pages = {}
    warnings = ['扫描文字由本机OCR识别；金额、日期、比例及付款条件须对照原附件核对']
    try:
        if _engine is None:
            _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1,
                               det_limit_side_len=1200, max_side_len=2400)
        with pdfium.PdfDocument(raw) as document:
            for number in page_numbers:
                page = document[number - 1]
                try:
                    width, height = page.get_size()
                    if min(width, height) <= 0:
                        raise ValueError('PDF页面尺寸无效')
                    scale = min(2.5, 2400 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        result, _ = _engine(bitmap.to_numpy())
                    finally:
                        bitmap.close()
                    lines = [str(item[1]).strip() for item in (result or []) if str(item[1]).strip()]
                    pages[number] = '\n'.join(lines)
                    if not lines:
                        warnings.append(f'PDF第{number}页未识别到文字，请人工补充')
                    elif any(float(item[2]) < .8 for item in result):
                        warnings.append(f'PDF第{number}页存在低置信度文字，请重点核对')
                finally:
                    page.close()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('本地扫描识别未完成，请核对PDF或改用人工摘录') from error
    finally:
        _ocr_lock.release()
    return pages, warnings
