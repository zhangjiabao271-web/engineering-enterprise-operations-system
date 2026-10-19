"""Isolated, atomic Excel transfer without desktop-widget dependencies."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager, closing
from pathlib import Path
from unittest.mock import patch

IMPORTS={'suppliers':'import_suppliers','offers':'import_products','purchases':'import_purchases'}
EXPORTS={'suppliers':'export_suppliers','offers':'export_products','purchases':'export_purchases','operating':'export_operating_workbook'}


@contextmanager
def bind_business_connections(conn):
    """Only use in a worker process: all services join one outer transaction."""
    @contextmanager
    def shared(*args, **kwargs):
        yield conn

    with ExitStack() as patches:
        for name, loaded in list(sys.modules.items()):
            if loaded and (name.startswith('services.') or name == 'db.connection'):
                for attribute in ('db_transaction', 'db_read'):
                    if hasattr(loaded, attribute):
                        patches.enter_context(patch.object(loaded, attribute, shared))
        from services import funds_service, historical_receivable_service
        patches.enter_context(patch.object(funds_service, '_connection', shared))
        patches.enter_context(patch.object(historical_receivable_service, 'transaction', shared))
        # Project creation already accepts a caller-owned connection. Bind the
        # stored Web callable as well, so a later failure rolls it back too.
        from services import project_service, web_workbench_service
        create_project = project_service.create_project

        def shared_project(data, *, connection=None):
            return create_project(data, connection=conn if connection is None else connection)

        patches.enter_context(patch.object(project_service, 'create_project', shared_project))
        patches.enter_context(patch.dict(web_workbench_service.SPECS['projects'], create=shared_project))
        yield


def run(database,operation,module,path,fingerprint=None):
    if module not in (IMPORTS if operation in ('preview','apply') else EXPORTS):
        raise ValueError('未开放此 Excel 操作')
    environment=os.environ.copy()
    environment['SUPPLY_CHAIN_DB_PATH']=str(database)
    environment['PYTHONIOENCODING']='utf-8'
    command=[sys.executable,'-B','-m','web_workbench.transfer','worker',operation,module,str(path),fingerprint or '']
    result=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],env=environment,
                          capture_output=True,text=True,encoding='utf-8',timeout=60)
    try:
        payload=json.loads(result.stdout.splitlines()[-1])
    except (IndexError,ValueError):
        raise ValueError('本机 Excel 操作未完成；未报告成功') from None
    if 'error' in payload:
        raise ValueError(payload['error'])
    return payload


def worker(operation, module, path, expected):
    import db.connection as connection
    from services import excel_transfer_service as excel
    from services import web_workbench_service as business
    from services.web_workbench_operations import database_fingerprint

    if operation not in ('preview', 'apply', 'export') or module not in (
            IMPORTS if operation in ('preview', 'apply') else EXPORTS):
        raise ValueError('未开放此 Excel 操作')
    database = Path(connection.DB_PATH).resolve()
    if not database.is_file():
        raise ValueError('明确选择的数据库不存在')
    importing = operation in ('preview', 'apply')
    with tempfile.TemporaryDirectory(prefix='engineering_excel_') as temporary:
        selected = database
        if operation != 'apply':
            selected = Path(temporary) / 'snapshot.db'
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as original, \
                 closing(sqlite3.connect(selected)) as snapshot:
                original.backup(snapshot)
        with closing(sqlite3.connect(selected)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            if not importing:
                conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN IMMEDIATE' if importing else 'BEGIN')
            try:
                fingerprint = database_fingerprint(conn)
                if operation == 'apply' and fingerprint != expected:
                    raise ValueError('经营资料在预览后已变化，本次 Excel 导入未提交，请重新预览')
                with bind_business_connections(conn):
                    if importing:
                        batch = excel.read_import(module, path)
                        created = excel.apply_import(module, batch, conn)
                        result = {'rows': [business.get_record(module, row['id']) for row in created],
                                  'row_count': batch['row_count'], 'record_count': len(created),
                                  'fingerprint': fingerprint,
                                  'messages': [f'成功核对 {batch["row_count"]} 行，保存 {len(created)} 条业务记录']}
                    else:
                        workbook = excel.build_workbook(module)
                        try:
                            for sheet in workbook:
                                for row in sheet:
                                    for cell in row:
                                        if cell.data_type == 'f':
                                            cell.data_type = 's'
                            exact = workbook.create_sheet('完整字段与精确分值')
                            exact.append(['台账', '记录编号', '字段', '值（整数分原样保存为文本）'])
                            for key in business.SPECS:
                                for row in business.rows(key):
                                    for field, value in business.get_record(key, row['id']).items():
                                        text = (json.dumps(value, ensure_ascii=False, default=str)
                                                if isinstance(value, (dict, list, set))
                                                else str(value) if value is not None else '')
                                        exact.append([business.SPECS[key]['title'], str(row['id']), field, text])
                                        exact.cell(exact.max_row, 4).data_type = 's'
                            workbook.save(path)
                        finally:
                            workbook.close()
                        result = {'messages': ['数据已导出']}
                if operation == 'apply':
                    conn.commit()
                else:
                    conn.rollback()
                return result
            except Exception:
                conn.rollback()
                raise


if __name__=='__main__':
    try:
        _,_,operation,module,path,expected=sys.argv
        print(json.dumps(worker(operation,module,path,expected),ensure_ascii=False,default=str))
    except Exception as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False))
        raise SystemExit(1)
