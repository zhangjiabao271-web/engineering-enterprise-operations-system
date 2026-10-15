"""Privacy-safe new-ledger setup; existing entities are never rewritten."""
from db.connection import db_read, db_transaction
from services._common import now

DEFAULT_ENTITIES = {'general': '示例钢结构有限公司', 'small': '示例建筑安装队'}


def initialize_new_ledger(path):
    """Called only after an empty database's first complete initialization."""
    with db_transaction(path, immediate=True) as conn:
        conn.execute('PRAGMA secure_delete=ON')
        for table in ('projects','contracts','purchase_orders','work_logs','settlements','sales_invoices',
                      'receipts','cost_entries','fund_transactions','historical_receipts'):
            if conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                raise ValueError('已有经营事实，不能按新安装初始化')
        if conn.execute('SELECT 1 FROM record_entities LIMIT 1').fetchone() or conn.execute('SELECT 1 FROM input_invoices LIMIT 1').fetchone():
            raise ValueError('已有主体业务，不允许按新安装初始化')
        for identity, name in DEFAULT_ENTITIES.items():
            conn.execute('UPDATE operating_entities SET name=? WHERE tax_identity=?', (name, identity))
    # Remove obsolete seed-name bytes from the brand-new file, not any user ledger.
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('VACUUM')


def setup_available(conn, entity):
    return (entity['name'] == DEFAULT_ENTITIES.get(entity['tax_identity'])
        and not conn.execute('SELECT 1 FROM record_entities WHERE entity_id=? LIMIT 1', (entity['id'],)).fetchone()
        and not conn.execute('SELECT 1 FROM input_invoices WHERE entity_id=? LIMIT 1', (entity['id'],)).fetchone())


def _checked_entity(conn, entity_id, name, reason):
    name = str(name or '').strip()
    if not name or name.startswith('示例') or not str(reason or '').strip():
        raise ValueError('请填写真实主体名称和首次设置说明，不能继续使用示例名称')
    if len(name) > 120:
        raise ValueError('主体名称过长')
    entity = conn.execute('SELECT * FROM operating_entities WHERE id=?', (int(entity_id),)).fetchone()
    if not entity or not setup_available(conn, entity):
        raise ValueError('此主体已设置或已有业务，不能通过首次设置改名；原归属未改变')
    if conn.execute('SELECT 1 FROM operating_entities WHERE name=? AND id<>?', (name, entity_id)).fetchone():
        raise ValueError('主体名称已存在')
    return entity, name


def preview_configuration(entity_id, name, tax_number='', reason=''):
    with db_read() as conn:
        entity, name = _checked_entity(conn, entity_id, name, reason)
        return {'before':dict(entity),'after':{**dict(entity),'name':name,'tax_number':str(tax_number or '').strip()}}


def configure_entity(entity_id, name, tax_number='', reason=''):
    with db_transaction(immediate=True) as conn:
        entity, name = _checked_entity(conn, entity_id, name, reason)
        conn.execute('UPDATE operating_entities SET name=?,tax_number=? WHERE id=?',
                     (name, str(tax_number or '').strip(), entity_id))
        conn.execute('''INSERT INTO operating_entity_audit
            (record_type,record_id,old_entity_id,new_entity_id,reason,created_at)
            VALUES ('operating_entities',?,?,?,?,?)''',
            (entity_id, entity_id, entity_id, f"首次设置：{entity['name']} → {name}；{str(reason).strip()}", now()))
    return entity_id
