"""Read-only, traceable ownership derived from explicit business relationships."""


class OwnershipResolver:
    def __init__(self, conn):
        self.conn = conn
        self.entities = {row['id']: dict(row) for row in conn.execute('SELECT * FROM operating_entities')}
        self.assignments = {(row['record_type'], row['record_id']): dict(row)
                            for row in conn.execute('SELECT * FROM record_entities')}
        self.projects = {row['id']: dict(row) for row in conn.execute('SELECT * FROM projects')}
        self.contracts = {row['id']: dict(row) for row in conn.execute("SELECT * FROM contracts WHERE status<>'void'")}
        self.links = {}
        for row in conn.execute("""SELECT project_id,contract_id FROM contract_project_allocations WHERE status='active'
                UNION SELECT project_id,contract_id FROM settlements WHERE status='active' AND contract_id IS NOT NULL
                UNION SELECT project_id,contract_id FROM sales_invoices WHERE status='active' AND contract_id IS NOT NULL
                UNION SELECT a.project_id,a.contract_id FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                    WHERE a.status='active' AND r.status='active' AND a.contract_id IS NOT NULL"""):
            if row['contract_id'] in self.contracts:
                self.links.setdefault(row['project_id'], set()).add(row['contract_id'])
        self.cache = {}

    def set_assignment(self, kind, record_id, entity_id):
        """Change only this resolver's preview; never write the ledger."""
        self.assignments[(kind, record_id)] = {'entity_id': entity_id, 'source': '本次确认'}
        self.cache.clear()

    def _result(self, entity_id=None, status='pending', source='', reason='', **extra):
        names = {'confirmed': '已确认', 'inherited': '已自动归属', 'pending': '待确认', 'conflict': '归属冲突'}
        return {'entity_id': entity_id,
                'entity_name': self.entities[entity_id]['name'] if entity_id in self.entities else '待确认',
                'ownership_status': status, 'ownership_label': names[status],
                'ownership_source': source, 'ownership_reason': reason, **extra}

    def _combine(self, resolutions, source, reason, *, shared_cost=False):
        known = {row['entity_id'] for row in resolutions if row['entity_id'] is not None}
        conflict = any(row['ownership_status'] == 'conflict' for row in resolutions)
        if shared_cost and len(known) > 1 and not conflict:
            return self._result(source='多主体项目分摊',
                                reason='项目成本已按份额分别统计；原采购／费用单据的实际购买主体仍需确认，不能按分摊猜测。')
        if len(known) > 1 or conflict:
            return self._result(status='conflict', source=source, reason='关联业务属于不同经营主体，请核对，不自动合并。')
        if resolutions and len(known) == 1 and all(row['entity_id'] is not None for row in resolutions):
            return self._result(next(iter(known)), 'inherited', source, reason)
        return self._result(source=source, reason='关联业务的经营主体尚未全部确认。')

    def contract(self, contract_id, visited=()):
        key = ('contracts', contract_id)
        if key in self.cache:
            return self.cache[key]
        contract = self.contracts.get(contract_id)
        if contract is None:
            return self._result(reason='合同不存在或已作废。')
        direct = self.assignments.get(key)
        if direct:
            result = self._result(direct['entity_id'], 'confirmed', '合同确认', direct['source'])
        elif contract_id in visited:
            result = self._result(status='conflict', reason='上级合同关系存在循环，请核对。')
        elif contract.get('parent_contract_id'):
            result = self._combine([self.contract(contract['parent_contract_id'], (*visited, contract_id))],
                                   '沿用原合同', '补充协议沿用原合同的经营主体。')
        else:
            result = self._result(reason='确认这份合同的经营主体后，关联项目会自动沿用。')
        self.cache[key] = result
        return result

    def project(self, project_id):
        key = ('projects', project_id)
        if key in self.cache:
            return self.cache[key]
        direct = self.assignments.get(key)
        contract_ids = sorted(self.links.get(project_id, ()))
        contract_results = [self.contract(identity) for identity in contract_ids]
        if contract_results and any(row['entity_id'] is not None or row['ownership_status'] == 'conflict'
                                    for row in contract_results):
            result = self._combine(contract_results, '沿用合同', '项目自动沿用关联合同的经营主体，无需重复确认。')
        elif direct:
            # Keep historical manual confirmations visible until a contract establishes ownership.
            source = '历史项目确认' if contract_ids else '无合同项目确认'
            result = self._result(direct['entity_id'], 'confirmed', source, direct['source'])
        else:
            result = self._result(source='沿用合同' if contract_ids else '无合同项目待确认',
                                  reason='请先确认关联合同。' if contract_ids else '无合同或零星工程需要确认实际经营主体。')
        result.update(contract_ids=contract_ids,
                      contract_names=[self.contracts[identity]['name'] for identity in contract_ids],
                      direct_entity_id=direct['entity_id'] if direct else None,
                      assignable=not contract_ids)
        self.cache[key] = result
        return result

    def record(self, kind, row):
        row = dict(row)
        identity = row['id']
        if kind == 'contracts':
            return {**self.contract(identity), 'assignable': True}
        if kind == 'projects':
            return self.project(identity)
        direct = self.assignments.get((kind, identity))
        if direct:
            return {**self._result(direct['entity_id'], 'confirmed', '单据确认', direct['source']), 'assignable': True}
        if kind == 'fund_accounts':
            return {**self._result(reason='资金账户按实际所属主体确认，不根据项目猜测。'), 'assignable': True}
        if kind == 'fund_transactions':
            sources = {'receipt': 'receipts', 'purchase': 'purchase_orders', 'cost': 'cost_entries'}
            source_kind = sources.get(row.get('source_kind'))
            if source_kind and row.get('source_id'):
                source = self.conn.execute(f'SELECT * FROM {source_kind} WHERE id=?', (row['source_id'],)).fetchone()
                if source:
                    return {**self._combine([self.record(source_kind, source)], '沿用业务单据', '沿用对应业务单据的经营主体。'),
                            'assignable': True}
            return {**self._result(reason='请核对实际资金归属，不由资金方向猜测。'), 'assignable': True}
        if kind == 'receipts':
            allocations = [dict(item) for item in self.conn.execute(
                "SELECT project_id,contract_id FROM receipt_allocations WHERE receipt_id=? AND status='active'", (identity,))]
            resolutions = [self.contract(item['contract_id']) if item['contract_id'] else self.project(item['project_id'])
                           for item in allocations]
            if not resolutions:
                resolutions = [self.contract(row['contract_id'])] if row.get('contract_id') else (
                    [self.project(row['project_id'])] if row.get('project_id') else [])
            result = self._combine(resolutions, '沿用回款分配', '沿用本笔回款的明确项目／合同分配。')
        elif row.get('contract_id'):
            result = self._combine([self.contract(row['contract_id'])], '沿用合同', '沿用本条记录明确关联的合同。')
        else:
            from services.operating_entity_service import project_ids
            result = self._combine([self.project(pid) for pid in project_ids(self.conn, kind, row)],
                                   '沿用项目', '沿用已明确归属的项目；不根据客户或供应商名称猜测。',
                                   shared_cost=kind in ('purchase_orders', 'cost_entries'))
        return {**result, 'assignable': True}
