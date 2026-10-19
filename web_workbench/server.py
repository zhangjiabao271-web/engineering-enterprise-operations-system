"""Loopback-only web entry. Imports services after selecting an explicit DB."""

import argparse
import base64
import json
import logging
import mimetypes
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / 'static'
MAX_BODY = 28 * 1024 * 1024
PDF_PREVIEW_GETS = frozenset({'/api/attachment_pdf_info', '/api/attachment_pdf_page'})
READ_ONLY_POSTS = frozenset({
    '/api/assistant/new', '/api/assistant/ask', '/api/assistant/stop',
    '/api/assistant/scope', '/api/assistant/confirm', '/api/purchase_preview', '/api/receipt_preview',
    '/api/backup/create', '/api/backup/restore_start', '/api/backup/restore_chunk',
    '/api/backup/restore_preview', '/api/backup/restore_discard',
    '/api/cashflow/preview', '/api/cashflow/attachment_preview', '/api/cashflow/discard',
})


def json_value(value, key=''):
    if isinstance(value, dict):
        return {k:json_value(v,k) for k,v in value.items()}
    if isinstance(value, (list,tuple,set)):
        return [json_value(v) for v in value]
    if isinstance(value,int) and (key.endswith(('_minor','_cents')) or key.endswith('_minor_total')):
        return str(value)
    return value


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, *, read_only, demo, staging=None):
        super().__init__(('127.0.0.1',port), Handler)
        self.read_only = read_only
        self.demo = demo
        from db.connection import DB_PATH
        self.rehearsal = Path(DB_PATH).resolve().is_relative_to(ROOT / 'qa')
        self.token = secrets.token_urlsafe(32)
        from web_workbench.assistant import WebAssistant
        self.assistant = WebAssistant()
        self.write_lock = threading.RLock()
        self.pdf_preview_slots = threading.BoundedSemaphore(2)
        self.uploads = {}
        self.previews = {}
        self.backups = {}
        self.temp = tempfile.TemporaryDirectory(prefix='engineering_web_') if staging is None else None
        self.staging = Path(staging or self.temp.name)
        self.staging.mkdir(parents=True,exist_ok=True)
        self.origin = f'http://127.0.0.1:{self.server_port}'

    def server_close(self):
        self.assistant.close()
        super().server_close()
        if self.temp:
            self.temp.cleanup()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logging.info('%s %s', self.command, self.path.split('?')[0])

    def send_content(self, raw, content_type='application/json; charset=utf-8', status=200, *, attachment=None, report=False, preview=False, inline_name=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        policy = "default-src 'self'; img-src 'self' data: blob:; connect-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if report:
            policy = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        if preview:
            policy = "default-src 'none'; frame-ancestors 'self'; base-uri 'none'"
        self.send_header('Content-Security-Policy',policy)
        if attachment:
            from urllib.parse import quote
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''" + quote(attachment))
        elif inline_name:
            from urllib.parse import quote
            self.send_header('Content-Disposition',"inline; filename*=UTF-8''" + quote(inline_name))
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, value, status=200):
        raw = json.dumps(json_value(value),ensure_ascii=False,default=str).encode('utf-8')
        self.send_content(raw,status=status)

    def check_host(self):
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            raise ValueError('本工作台仅接受本机回环地址访问')

    def do_GET(self):
        try:
            self.check_host()
            path = urlsplit(self.path).path
            query = {k:v[0] for k,v in parse_qs(urlsplit(self.path).query,keep_blank_values=True).items()}
            if not path.startswith('/api/'):
                if path in ('/cockpit.js', '/cockpit.css', '/entities.js', '/entities.css','/attention.js','/attention.css','/entry-draft.js','/governance.js','/governance.css','/receipt-income.js','/contract-ledger.js','/contract-ledger.css','/contract-project-business.js','/purchase-allocation.js','/partners.js','/partners.css'):
                    filename = path[1:]
                    self.send_content((STATIC/filename).read_bytes(), mimetypes.guess_type(filename)[0]+'; charset=utf-8')
                    return
                filename = {'/':'index.html','/app.js':'app.js','/labels.js':'labels.js','/styles.css':'styles.css','/shell.css':'shell.css','/motion.js':'motion.js','/navigation.js':'navigation.js','/table-scrollbar.js':'table-scrollbar.js','/operations.js':'operations.js','/extensions.js':'extensions.js','/assistant.js':'assistant.js','/forms.js':'forms.js','/labor.js':'labor.js','/backup.js':'backup.js','/attachment-preview.js':'attachment-preview.js','/attachment-preview.css':'attachment-preview.css','/cost-ledger.js':'cost-ledger.js','/project-ledger.js':'project-ledger.js','/project-ledger.css':'project-ledger.css'}.get(path)
                if not filename:
                    self.send_json({'error':'页面不存在'},404)
                    return
                self.send_content((STATIC/filename).read_bytes(), mimetypes.guess_type(filename)[0]+'; charset=utf-8')
                return
            if path in PDF_PREVIEW_GETS:
                # PDFium is isolated and read-only; never hold the ledger lock
                # while waiting up to twenty seconds for a page to render.
                if not self.server.pdf_preview_slots.acquire(blocking=False):
                    raise ValueError('附件预览正在处理其他页面，请稍后重试；其他台账可以正常使用')
                try:
                    self.get_api(path,query)
                finally:
                    self.server.pdf_preview_slots.release()
            else:
                with self.server.write_lock:
                    self.get_api(path,query)
        except (ValueError,KeyError,TypeError) as error:
            self.send_json({'error':str(error)},400)
        except Exception:
            logging.exception('网页读取失败')
            self.send_json({'error':'读取未完成，请核对日志；原记录未因此修改'},500)

    def get_api(self,path,query):
        from services import web_workbench_service as business
        from services import document_cashflow_service as cashflow
        from services import web_workbench_operations as operations
        project_id = int(query['project_id']) if query.get('project_id') else None
        contract_id = int(query['contract_id']) if query.get('contract_id') else None
        scope = {'contract_id':contract_id,'project_id':project_id}
        from web_workbench.backup import get_api as get_backup
        if get_backup(self, path, query):
            return
        from web_workbench.extended_api import get_api as get_extended
        if path == '/api/assistant/state':
            if self.headers.get('X-Workbench-Token') != self.server.token:
                raise ValueError('请从当前工作台重新打开对话。')
            self.send_json(self.server.assistant.snapshot(query['session_id']))
            return
        if get_extended(self,path,query):
            return
        if path == '/api/bootstrap':
            self.send_json({'navigation':business.NAVIGATION,'schema':business.schema(),'options':business.options(),
                            'read_only':self.server.read_only,'demo':self.server.demo,'rehearsal':self.server.rehearsal,'csrf':self.server.token,
                            'operations':operations.OPERATIONS,'blocked':operations.BLOCKED,
                            'database_label':Path(os.environ.get('SUPPLY_CHAIN_DB_PATH','supplier_data.db')).name})
        elif path == '/api/excel_export':
            from web_workbench import transfer
            from db.connection import DB_PATH
            destination=self.server.staging/(secrets.token_hex(24)+'.xlsx')
            try:
                transfer.run(DB_PATH,'export',query.get('module','operating'),destination)
                raw=destination.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.send_header('Content-Disposition','attachment; filename="engineering-export.xlsx"')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
            finally:destination.unlink(missing_ok=True)
        elif path == '/api/operation_read':
            self.send_json(operations.read(query['name'],query))
        elif path == '/api/options':
            self.send_json(business.options(project_id))
        elif path == '/api/rows':
            self.send_json({'rows':business.rows(query['module'],project_id,query.get('keyword',''),
                supplier_id=query.get('supplier_id') or None,customer_id=query.get('customer_id') or None,
                year=query.get('year') or None)})
        elif path == '/api/attention':
            from services.operating_attention_service import overview
            self.send_json(overview(project_id))
        elif path == '/api/project_ledger':
            self.send_json(business.project_ledger.get_details(int(query['id'])))
        elif path == '/api/contract_ledger':
            from services.contract_ledger_service import get_overview
            self.send_json(get_overview(project_id=project_id, customer_key=query.get('customer',''),
                year=query.get('year',''), status=query.get('status',''),
                entity_id=query.get('entity_id',''), keyword=query.get('keyword',''),business_scope=query.get('business_scope')))
        elif path == '/api/contract_business_detail':
            from services.contract_ledger_service import get_business_details
            self.send_json(get_business_details(project_id=project_id,customer_key=query.get('customer',''),
                year=query.get('year',''),status=query.get('status',''),entity_id=query.get('entity_id',''),
                keyword=query.get('keyword',''),business_scope=query.get('business_scope')))
        elif path == '/api/contract_project_business':
            from services.contract_ledger_service import get_project_business_details
            self.send_json(get_project_business_details(int(query['id']),scope_project_id=project_id,
                entity_id=query.get('entity_id','')))
        elif path == '/api/contract_ledger_detail':
            from services.contract_ledger_service import get_details
            self.send_json(get_details(int(query['id']),project_id,entity_id=query.get('entity_id',''),
                business_scope=query.get('business_scope','contracts')))
        elif path == '/api/record':
            self.send_json(business.get_record(query['module'],int(query['id'])))
        elif path == '/api/dashboard':
            self.send_json(business.dashboards(query['view'],project_id,query.get('month') or None,
                year=query.get('year') or None,customer_id=query.get('customer_id') or None))
        elif path == '/api/cockpit_detail':
            from services.cockpit_service import get_detail
            self.send_json(get_detail(query['metric'], project_id, query.get('month') or None))
        elif path == '/api/cashflow/documents':
            self.send_json(cashflow.list_documents(**scope,include_history=query.get('history')=='1'))
        elif path == '/api/cashflow/analyses':
            self.send_json(cashflow.list_analyses(**scope))
        elif path == '/api/cashflow/analysis':
            self.send_json(cashflow.get_analysis(int(query['id'])))
        elif path == '/api/cashflow/attachments':
            self.send_json([{k:r[k] for k in ('id','original_name','category','file_exists','already_linked')}
                            for r in cashflow.available_attachments(**scope)])
        elif path == '/api/cashflow/report':
            from services.document_cashflow_report import export_html
            report = self.server.staging / (secrets.token_hex(12)+'.html')
            export_html(int(query['id']),report)
            self.send_content(report.read_bytes(),'text/html; charset=utf-8',report=True)
            report.unlink()
        elif path == '/api/attachments':
            from services.attachment_service import list_attachments
            from web_workbench.attachment_preview import preview_metadata
            self.send_json([preview_metadata(row) for row in list_attachments(query['entity'],int(query['id']),include_void=query.get('history')=='1')])
        elif path == '/api/attachment':
            from web_workbench.attachment_preview import preview_type, resolve_attachment
            row, file = resolve_attachment(query)
            viewing = query.get('view') == '1'
            content_type = preview_type(row['original_name']) if viewing else 'application/octet-stream'
            if viewing and not content_type:
                raise ValueError('此格式暂不支持预览，请下载原附件查看')
            self.send_content(file.read_bytes(), content_type,
                              attachment=None if viewing else row['original_name'],
                              preview=viewing, inline_name=row['original_name'] if viewing else None)
        elif path in ('/api/attachment_pdf_info', '/api/attachment_pdf_page'):
            from web_workbench.attachment_preview import preview_type, resolve_attachment, pdf_preview
            row, file = resolve_attachment(query)
            if preview_type(row['original_name']) != 'application/pdf':
                raise ValueError('请选择PDF附件')
            if path == '/api/attachment_pdf_info':
                self.send_json(pdf_preview(file))
            else:
                page = int(query.get('page', '1'))
                if page < 1:
                    raise ValueError('PDF页码超出范围')
                self.send_content(pdf_preview(file, page), 'image/png', preview=True)
        elif path == '/api/sites':
            self.send_json(business.projects.list_project_sites(project_id))
        elif path == '/api/fund_sources':
            self.send_json(business.funds.list_sources(query['kind'],include_closed=True))
        else:
            self.send_json({'error':'接口不存在'},404)

    def do_POST(self):
        try:
            self.check_host()
            if self.headers.get('Origin') != self.server.origin or self.headers.get('X-Workbench-Token') != self.server.token:
                self.send_json({'error':'请求来源校验未通过，请从本工作台页面重新操作'},403)
                return
            path = urlsplit(self.path).path
            if self.server.read_only and path not in READ_ONLY_POSTS:
                self.send_json({'error':'当前为只读运行；如需记账，请由使用者明确启用写入模式'},403)
                return
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= MAX_BODY:
                raise ValueError('上传或请求超过28MB限制')
            if not self.headers.get('Content-Type','').startswith('application/json'):
                raise ValueError('请求格式不支持')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data,dict):
                raise ValueError('请求必须为对象')
            from web_workbench.cashflow_sources import PREVIEW_PATHS
            lock = nullcontext() if path in PREVIEW_PATHS else self.server.write_lock
            with lock:
                result = self.post_api(path,data)
            self.send_json({'result':result})
        except (ValueError,KeyError,TypeError) as error:
            self.send_json({'error':str(error)},400)
        except Exception:
            logging.exception('网页操作未完成')
            self.send_json({'error':'操作未完成，状态待核对。请保留输入并查看日志，不要重复提交。'},500)

    def staged_upload(self,data):
        name = Path(str(data.get('name') or '')).name
        if not name or len(name)>180:
            raise ValueError('文件名无效')
        raw = base64.b64decode(data['content'],validate=True)
        if not raw or len(raw)>20*1024*1024:
            raise ValueError('请选择不超过20MB的文件')
        token = secrets.token_hex(24)
        path = self.server.staging / (token+Path(name).suffix.lower())
        path.write_bytes(raw)
        return token,path,name

    def post_api(self,path,data):
        if path.startswith('/api/attention/'):
            from services import operating_attention_service as attention
            if path == '/api/attention/day_open':
                return attention.start_day()
            if data.get('confirmed') is not True:
                raise ValueError('请明确确认自检或跟进操作')
            if path == '/api/attention/check':
                return attention.set_check(data.get('kind'),data.get('day'),data.get('status'),data.get('basis'))
            if path == '/api/attention/manage':
                return attention.set_attention(data.get('key'),data.get('action'),data.get('basis'),data.get('until_day'))
            raise ValueError('自检或经营跟进操作不存在')
        if path.startswith('/api/entry/'):
            from services import assistant_entry_service as entries
            if path == '/api/entry/preview':
                return entries.preview(data.get('records'),request_key=data.get('request_key'),project_id=data.get('project_id'),
                                       model_questions=data.get('model_questions'),model_reviewed=data.get('model_reviewed',False))
            if path == '/api/entry/apply':
                return entries.apply(data.get('token'),confirmed=data.get('confirmed'))
            if path == '/api/entry/discard':
                return entries.discard(data.get('token'))
            raise ValueError('助手录入操作不存在')
        if path.startswith('/api/backup/'):
            from web_workbench.backup import post_api as post_backup
            return post_backup(self.server, path, data)
        if path.startswith('/api/assistant/'):
            assistant = self.server.assistant
            if path == '/api/assistant/new':
                return assistant.create()
            if path == '/api/assistant/ask':
                if self.server.read_only and data.get('mode') == 'entry':
                    raise ValueError('只读工作台不能生成记账清单')
                return assistant.ask(data['session_id'], data.get('question'), allow_model=data.get('allow_model', False),
                                     expected_generation=data.get('generation'),mode=data.get('mode','query'))
            if path == '/api/assistant/stop':
                return assistant.stop(data['session_id'])
            if path == '/api/assistant/entry_completed':
                if self.server.read_only:
                    raise ValueError('只读工作台不能登记录入结果')
                return assistant.entry_completed(data['session_id'],data.get('generation'),data.get('token'))
            if path == '/api/assistant/confirm':
                return assistant.confirm(data['session_id'], data.get('generation'), data.get('index'),
                                         allow_model=data.get('allow_model', False))
            if path == '/api/assistant/scope':
                project_id = int(data['project_id']) if data.get('project_id') else None
                if project_id:
                    from services.project_service import get_project
                    if not get_project(project_id):
                        raise ValueError('项目不存在，请重新选择。')
                return assistant.change(data['session_id'], project_id=project_id, clear=data.get('clear') is True)
            raise ValueError('经营助手操作不存在。')
        from services import web_workbench_service as business
        if path == '/api/purchase_preview':
            return business.purchase_preview(data.get('items'), data.get('freight', '0'),
                allocation_method=data.get('allocation_method'), project_ids=data.get('project_ids'))
        if path == '/api/receipt_preview':
            return {'rows': business.finance.preview_receipt_allocations(data.get('record', {}),
                exclude_receipt_id=int(data['id']) if data.get('id') else None)}
        from services import document_cashflow_service as cashflow
        scope = {k:int(data[k]) if data.get(k) else None for k in ('contract_id','project_id')}
        from web_workbench.cashflow_sources import SOURCE_PATHS, post_source
        if path in SOURCE_PATHS:
            return post_source(self, path, data, scope)
        if path.startswith(('/api/csv_','/api/evidence_','/api/pdf_')) or path in ('/api/photos_upload','/api/upload_attachment','/api/write_discard'):
            from web_workbench.extended_api import post_api as post_extended
            return post_extended(self,path,data)
        if path == '/api/input_pdf_preview':
            from services import input_invoice_service as inputs
            from pypdf import PdfReader
            token,file,name=self.staged_upload(data)
            try:
                if file.suffix!='.pdf':raise ValueError('请选择文字型PDF；此发票入口暂不支持扫描识别')
                if len(PdfReader(file).pages)>200:raise ValueError('PDF 超过200页，请拆分资料')
                result=inputs.recognize_pdf(file)
                from web_workbench.extended_api import fingerprint
                self.server.uploads[token]={'source_path':str(file),'name':name,'recognition':result,
                    'fingerprint':fingerprint(),'created':time.monotonic()}
                return {'token':token,'recognition':result,'name':name}
            except Exception:
                file.unlink(missing_ok=True);raise
        if path == '/api/input_pdf_attach':
            from web_workbench import extended
            from web_workbench.extended_api import request_digest
            if data.get('confirmed') is not True:raise ValueError('请核对 PDF 来源并确认关联')
            preview=self.server.uploads.get(data['token'])
            if not preview or 'recognition' not in preview or time.monotonic()-preview['created']>1800:
                raise ValueError('PDF 预览已取消或失效')
            digest=request_digest(data)
            if 'applied' in preview:
                if preview['request_digest']!=digest:raise ValueError('此 PDF 已确认，不能用旧预览关联不同记录')
                return preview['applied']
            result=extended.run('input_attach_apply',{**preview,'id':int(data['id']),'expected':data.get('expected')})
            preview.update(applied=result,request_digest=digest)
            Path(preview['source_path']).unlink(missing_ok=True)
            return result
        if path == '/api/excel_preview':
            from web_workbench import transfer
            from db.connection import DB_PATH
            token,file,name=self.staged_upload(data)
            try:
                if file.suffix!='.xlsx':raise ValueError('请选择原有模板格式的 .xlsx 文件')
                review=transfer.run(DB_PATH,'preview',data['module'],file)
                self.server.previews[token]={'transfer':True,'module':data['module'],'path':file,
                    'fingerprint':review['fingerprint'],'created':time.monotonic()}
                return {'token':token,'review':review}
            except Exception:
                file.unlink(missing_ok=True);raise
        if path == '/api/excel_discard':
            preview=self.server.previews.pop(data['token'],None)
            if preview and preview.get('transfer'):preview['path'].unlink(missing_ok=True)
            return True
        if path == '/api/excel_apply':
            from web_workbench import transfer
            from db.connection import DB_PATH
            if data.get('confirmed') is not True:raise ValueError('请明确确认整批导入')
            preview=self.server.previews.get(data['token'])
            if not preview or not preview.get('transfer') or time.monotonic()-preview['created']>1800:
                raise ValueError('导入预览已取消或失效，请重新预览')
            if 'result' in preview:return preview['result']
            result=transfer.run(DB_PATH,'apply',preview['module'],preview['path'],preview['fingerprint'])
            preview['result']=result
            preview['path'].unlink(missing_ok=True)
            return result
        if path == '/api/operation_preview':
            from services import web_workbench_operations as operations
            values,review,fingerprint = operations.run_worker('preview',{'name':data['name'],'record':data['record']})
            token=secrets.token_hex(24)
            self.server.previews[token]={'name':data['name'],'values':values,'fingerprint':fingerprint,'created':time.monotonic()}
            return {'token':token,'review':review}
        if path == '/api/operation_discard':
            self.server.previews.pop(data['token'],None)
            return True
        if path == '/api/operation_apply':
            from services import web_workbench_operations as operations
            from db.connection import db_read
            if data.get('confirmed') is not True:
                raise ValueError('请核对预览并明确确认')
            preview=self.server.previews.get(data['token'])
            if not preview or time.monotonic()-preview['created']>1800:
                raise ValueError('预览已取消或超过30分钟，请重新预览')
            if 'result' in preview:
                return preview['result']
            with db_read() as conn:
                if operations.database_fingerprint(conn)!=preview['fingerprint']:
                    raise ValueError('业务资料在预览后已变化，请重新预览；本次操作未提交')
            result=operations.run_worker('apply',{'name':preview['name'],'values':preview['values'],'fingerprint':preview['fingerprint'],'token':data['token']})
            preview['result']=result
            return result
        if path == '/api/save':
            if data.get('confirmed') is not True:
                raise ValueError('请核对本笔业务并明确确认保存')
            from web_workbench.extended_api import guarded_write
            from web_workbench import extended
            return guarded_write(self,path,data,lambda ticket:extended.run('save_apply',{**data,'fingerprint':ticket['fingerprint']}))
        if path == '/api/action':
            if data.get('confirmed') is not True:
                raise ValueError('请明确确认此业务操作')
            from web_workbench.extended_api import guarded_write
            from web_workbench import extended
            return guarded_write(self,path,data,lambda ticket:extended.run('action_apply',{**data,'fingerprint':ticket['fingerprint']}))
        if path == '/api/cashflow/retire':
            return cashflow.retire_document(int(data['id']),data['reason'])
        if path == '/api/cashflow/analyze':
            return cashflow.create_analysis(**scope, opening_minor=business.money_input(data.get('opening','0')),
                income_delay_days=int(data.get('delay',0)),expense_increase_percent=int(data.get('increase',0)))
        if path == '/api/cashflow/cancel':
            return cashflow.cancel_analysis(int(data['id']))
        if path == '/api/cashflow/confirm':
            return cashflow.confirm_analysis(int(data['id']),data['keys'],acknowledged=data.get('acknowledged'),reason=data.get('reason',''))
        raise ValueError('接口不存在或此操作尚未迁移')


def main():
    import sys
    sys.path.insert(0,str(ROOT))
    parser = argparse.ArgumentParser(description='现有工程工作台的本机网页入口，保留桌面与业务服务')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--demo',type=Path,help='全新隔离目录；生成合成库，不使用真实经营数据')
    group.add_argument('--db',type=Path,help='使用者明确选择的现有数据库；不会自动建库或迁移')
    parser.add_argument('--allow-write',action='store_true',help='明确启用已选择数据库的业务录入；否则真实库只读')
    parser.add_argument('--open-browser',action='store_true',help='启动后打开本机浏览器')
    parser.add_argument('--port',type=int,default=0,help='仅127.0.0.1；默认自动分配空闲端口')
    args = parser.parse_args()
    from web_workbench.runtime import prepare_database
    try:
        prepare_database(db=args.db, demo=args.demo, allow_write=args.allow_write)
    except ValueError as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    from contextlib import nullcontext
    from db.application_lock import ledger_writer
    lock = ledger_writer(os.environ['SUPPLY_CHAIN_DB_PATH']) if args.demo or args.allow_write else nullcontext()
    with lock:
        server = WorkbenchServer(args.port,read_only=not (args.demo or args.allow_write),demo=bool(args.demo))
        print(json.dumps({'url':server.origin,'demo':server.demo,'read_only':server.read_only,
                          'database':os.environ['SUPPLY_CHAIN_DB_PATH']},ensure_ascii=False),flush=True)
        if args.open_browser:
            import webbrowser
            webbrowser.open(server.origin)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
