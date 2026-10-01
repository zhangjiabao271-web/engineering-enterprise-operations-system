"""Opt-in receipt confirmation policy; no existing financial rows are changed."""


def upgrade(conn):
    conn.execute('''CREATE TABLE contract_receipt_income_policies (
        contract_id INTEGER PRIMARY KEY REFERENCES contracts(id),
        enabled_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE receipt_confirmed_settlements (
        receipt_id INTEGER NOT NULL REFERENCES receipts(id),
        settlement_id INTEGER NOT NULL UNIQUE REFERENCES settlements(id),
        created_at TEXT NOT NULL,
        PRIMARY KEY(receipt_id, settlement_id))''')


MIGRATIONS = [(620, '年度合同回款补足结算规则与追溯关联', upgrade)]
