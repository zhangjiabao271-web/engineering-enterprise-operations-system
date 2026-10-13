"""Customer-change boundaries shared by the project and contract services."""


def guard_customer_change(conn, kind, record_id, customer_id):
    if kind not in ('projects', 'contracts'):
        raise ValueError('不支持的客户归属变更')
    original = conn.execute(f'SELECT customer_partner_id FROM {kind} WHERE id=?',
                            (record_id,)).fetchone()
    if not original or original['customer_partner_id'] == customer_id:
        return

    from services.entity_ownership import OwnershipResolver
    relationships = OwnershipResolver(conn)
    if kind == 'projects':
        counterparts = [relationships.contracts[identity]
                        for identity in relationships.links.get(record_id, ())]
        label = '项目'
        column = 'project_id'
    else:
        counterparts = [relationships.projects[identity]
                        for identity, contracts in relationships.links.items() if record_id in contracts]
        label = '合同'
        column = 'contract_id'
    for related in counterparts:
        known_customer = related.get('customer_partner_id')
        if known_customer is not None and known_customer != customer_id:
            raise ValueError('项目与关联合同须属于同一客户，不能直接更换为其他客户或清空；'
                             '请先核对历史归属，原记录未改动')

    has_facts = conn.execute(f'''SELECT 1 WHERE
        EXISTS (SELECT 1 FROM settlements WHERE {column}=? AND status='active') OR
        EXISTS (SELECT 1 FROM sales_invoices WHERE {column}=? AND status='active') OR
        EXISTS (SELECT 1 FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                WHERE a.{column}=? AND a.status='active' AND r.status='active')''',
        (record_id, record_id, record_id)).fetchone()
    if original['customer_partner_id'] is not None and (counterparts or has_facts):
        raise ValueError(f'{label}已有合同项目关联、确认收入、发票或回款，不能直接更换客户；'
                         '请先核对历史归属，原记录未改动')
