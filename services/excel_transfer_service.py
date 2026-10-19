"""Headless Excel templates and adapters over the existing business services."""


from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from openpyxl import Workbook, load_workbook

from services import contract_service, cost_service, finance_service, master_data_service, project_service, procurement_service


def import_decimal(value, label, default=None):
    if value is None or value == "":
        if default is None:
            raise ValueError(f"{label}不能为空（公式需先在 Excel 中计算并保存）")
        value = default
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{label}必须为数字") from None
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label}必须为非负有效数字")
    return number


def import_minor(value):
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def import_settlement_fields(row, columns):
    if "计价方式" not in columns:
        return {}
    mode = row[columns["计价方式"]] or "quantity"
    mode = {label: key for key, label in procurement_service.SETTLEMENT_MODES.items()}.get(mode, mode)
    result = {"settlement_mode": mode}
    for label, key in (("实际净重", "net_weight"), ("过磅单位", "weight_unit"), ("重量单价", "weight_unit_price"), ("磅单号", "weigh_ticket_no")):
        if label in columns:
            result[key] = row[columns[label]]
    if mode == "total":
        if "整批结算金额" not in columns:
            raise ValueError("按结算总额需要整批结算金额列")
        result["settlement_total_cents"] = import_minor(import_decimal(row[columns["整批结算金额"]], "整批结算金额"))
    return result


def _operating_workbook():
    workbook = Workbook()
    workbook.remove(workbook.active)
    def add_sheet(name, headers, rows):
        sheet = workbook.create_sheet(name)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
    projects = project_service.list_projects()
    add_sheet(
        "项目",
        ["项目编码", "项目名称", "客户", "状态", "负责人", "计划开始", "计划结束"],
        [
            [
                row["project_code"], row["name"], row["customer_name"],
                row["status"], row["manager"], row["planned_start_date"],
                row["planned_end_date"],
            ]
            for row in projects
        ],
    )
    add_sheet(
        "合同",
        [
            "合同编号", "合同名称", "客户", "类型", "签订日期",
            "计价方式", "约定金额", "控制上限", "累计确认",
            "涉及项目数", "状态",
        ],
        [
            [
                row["contract_no"], row["name"], row["customer_name"],
                contract_service.CONTRACT_TYPES[row["contract_type"]],
                row["sign_date"],
                contract_service.PRICING_MODES[row["pricing_mode"]],
                (
                    None if row["pricing_mode"] == "actual"
                    else row["tax_inclusive_amount_minor"] / 100
                ),
                (
                    row["control_limit_minor"] / 100
                    if row["control_limit_minor"] is not None else None
                ),
                row["settled_minor"] / 100,
                row["project_count"],
                contract_service.CONTRACT_STATUSES[row["status"]],
            ]
            for row in contract_service.list_contracts(include_void=True)
        ],
    )
    add_sheet(
        "合同项目分配",
        ["合同编号", "合同名称", "项目编码", "项目名称", "分配金额", "说明"],
        [
            [
                row["contract_no"], row["contract_name"],
                row["project_code"], row["project_name"],
                row["allocated_amount_minor"] / 100, row["notes"],
            ]
            for row in contract_service.list_allocations()
        ],
    )
    add_sheet(
        "结算",
        [
            "结算编号", "日期", "项目", "合同", "结算金额",
            "已开票金额", "开票比例", "待开票金额", "依据",
        ],
        [
            [
                row["settlement_no"], row["settlement_date"],
                row["project_name"], row["contract_no"],
                row["amount_minor"] / 100,
                row["invoiced_minor"] / 100,
                row["invoice_rate_percent"],
                row["uninvoiced_minor"] / 100,
                row["basis"],
            ]
            for row in contract_service.list_settlements()
        ],
    )
    add_sheet(
        "销项发票",
        [
            "发票号码", "日期", "项目", "合同", "收入确认",
            "购买方", "税率", "金额",
        ],
        [
            [
                row["invoice_no"], row["invoice_date"], row["project_name"],
                row["contract_no"], row["settlement_no"],
                row["buyer_name_snapshot"],
                row["tax_rate_bps"] / 100, row["amount_minor"] / 100,
            ]
            for row in finance_service.list_invoices()
        ],
    )
    add_sheet(
        "回款记录",
        ["回款单号", "日期", "项目", "合同", "付款方", "方式", "金额"],
        [
            [
                row["receipt_no"], row["receipt_date"], row["project_name"],
                row["contract_no"], row["payer_name_snapshot"],
                row["payment_method"], row["allocated_amount_minor"] / 100,
            ]
            for row in finance_service.list_receipts()
        ],
    )
    project_names = {row["id"]: row["name"] for row in projects}
    add_sheet(
        "成本",
        ["日期", "项目", "来源单号", "分类", "往来单位或人员", "车辆/车牌", "来源", "金额"],
        [
            [
                row["business_date"], row.get("allocation_project_names")
                or project_names.get(row["project_id"], "待归集"),
                row["source_no"], row["category"], row["counterparty"],
                row.get("vehicle_no", ""), row["source_type"],
                row["amount_minor"] / 100,
            ]
            for row in cost_service.list_cost_ledger()
        ],
    )
    return workbook


def _suppliers_workbook():
    wb = Workbook()
    ws = wb.active
    ws.title = "供应商"
    headers = [
        "ID", "工厂名称", "产品范围", "联系人", "价格水平", "交期",
        "质量情况", "是否做出口", "备注", "默认税率（%）"
    ]
    ws.append(headers)
    for row in master_data_service.list_suppliers(active_only=False):
        ws.append([row["id"], row["name"], row["category"], row["contact"], row["price_level"],
                   row["delivery"], row["quality"], row["export"], row["notes"],
                   row["default_tax_rate_percent"]])
    return wb


def _offers_workbook():
    wb = Workbook()
    ws = wb.active
    ws.title = "产品"
    headers = [
        "ID", "供应商ID", "供应商名称", "产品名称", "规格", "单位",
        "材料单价（未税）", "税率（%）", "含税单价", "备注", "报价口径"
    ]
    ws.append(headers)
    for row in master_data_service.list_supplier_offers(active_only=False):
        ws.append([row["id"], row["supplier_id"], row["supplier_name"], row["name"],
                   row["specification"], row["unit"], row["price"],
                   row["tax_rate_percent"], row["tax_inclusive_price"], row["notes"], row["price_basis"]])
    return wb


def _purchases_workbook():
    wb = Workbook()
    ws = wb.active
    ws.title = "采购记录"
    headers = [
        "ID", "采购单号", "采购类型", "采购日期", "项目", "供应商ID", "产品ID",
        "供应商/商户", "材料名称", "规格", "单位", "数量",
        "材料单价（未税）", "税率（%）", "含税单价", "未税材料额",
        "税额", "含税材料额", "运费", "计入项目成本",
        "成本类别", "支付方式", "支付状态", "票据状态", "经办人", "用途", "备注", "报价口径",
        "计价方式", "实际净重", "过磅单位", "重量单价", "整批结算金额", "磅单号"
    ]
    ws.append(headers)
    exported_orders = set()
    for row in procurement_service.list_purchase_orders(detail_rows=True):
        freight = row["freight_amount_cents"] if row["id"] not in exported_orders else 0
        exported_orders.add(row["id"])
        merchant = row["merchant_name_snapshot"]
        if not merchant and row["supplier_id"]:
            supplier = master_data_service.get_supplier(row["supplier_id"])
            merchant = supplier['name'] if supplier else ''
        ws.append([
            row["id"], row["order_no"], row["purchase_type"], row["purchase_date"],
            row["project_name"] or "", row["supplier_id"], row["product_id"],
            merchant, row["material_name_snapshot"],
            row["specification_snapshot"], row["unit_snapshot"], row["quantity"],
            row["material_unit_price_cents"] / 100, row["tax_rate_bps"] / 100,
            row["tax_inclusive_unit_price_cents"] / 100,
            row["material_amount_cents"] / 100, row["tax_amount_cents"] / 100,
            row["line_amount_cents"] / 100, freight / 100,
            (row["line_amount_cents"] + freight) / 100, row["cost_category"],
            row["payment_method"], row["payment_status"],
            row["invoice_status"], row["purchaser"], row["purpose"],
            row["notes"] or row["item_notes"], row["price_basis"],
            procurement_service.SETTLEMENT_MODES[row["settlement_mode"]], row["net_weight"], row["weight_unit"], row["weight_unit_price"],
            row["settlement_total_cents"] / 100 if row["settlement_total_cents"] is not None else None, row["weigh_ticket_no"]
        ])
    return wb


def build_workbook(module):
    factories = {'suppliers': _suppliers_workbook, 'offers': _offers_workbook,
                 'purchases': _purchases_workbook, 'operating': _operating_workbook}
    if module not in factories:
        raise ValueError('未开放此 Excel 导出')
    return factories[module]()


def _cell(row, index, default=''):
    return row[index] if index < len(row) and row[index] is not None else default


def _supplier(row, _headers):
    if not _cell(row, 1):
        raise ValueError('供应商名称不能为空')
    return {'name': row[1], 'category': _cell(row, 2) or '钢材',
            'contact': _cell(row, 3), 'price_level': _cell(row, 4) or '中',
            'delivery': _cell(row, 5) or '一般', 'quality': _cell(row, 6) or '良',
            'export': _cell(row, 7) or '否', 'notes': _cell(row, 8),
            'default_tax_rate_percent': _cell(row, 9, 0)}


def _offer(row, headers):
    if not _cell(row, 1) or not _cell(row, 3):
        raise ValueError('供应商编号和产品名称不能为空')
    current = '税率（%）' in headers
    data = {'supplier_id': int(row[1]), 'name': row[3],
            'specification': _cell(row, 4), 'unit': _cell(row, 5),
            'price': import_decimal(_cell(row, 6, 0), '报价'),
            'notes': _cell(row, 9 if current else 7)}
    if current and _cell(row, 7, None) is not None:
        data['tax_rate_percent'] = import_decimal(row[7], '税率')
    if '报价口径' in headers:
        data['price_basis'] = _cell(row, headers.index('报价口径')) or 'exclusive'
        if data['price_basis'] == 'inclusive':
            if '含税单价' not in headers:
                raise ValueError('含税报价需要含税单价列')
            data['price'] = import_decimal(_cell(row, headers.index('含税单价'), None), '含税单价')
    return data


def _purchase(row, columns, projects):
    current = '采购类型' in columns and '供应商/商户' in columns
    taxed = '材料单价（未税）' in columns and '运费' in columns
    if not current:
        # Preserve old identifier lookup, but imports still need an explicit order number.
        raise ValueError('网页整批导入请补采购单号；请先转换为当前采购模板')
    if not all(_cell(row, index) for index in (2, 7, 8)):
        raise ValueError('采购类型、供应商/商户、材料名称不能为空')
    if row[2] not in ('正式采购', '零星采购'):
        raise ValueError('采购类型只能为正式采购或零星采购')
    if not str(_cell(row, 1)).strip():
        raise ValueError('网页整批导入请补采购单号，不能凭无编号资料确定是否重复业务')
    project_name = str(_cell(row, 4)).strip()
    matches = projects.get(project_name, [])
    if len(matches) > 1:
        raise ValueError('项目名称存在歧义，请使用唯一的项目名称，不能自动归到第一项')
    quantity = import_decimal(_cell(row, 11, None), '数量')
    unit_price = import_decimal(_cell(row, 12, None), '单价')
    if taxed:
        def value(name, default=''):
            return _cell(row, columns[name], default) if name in columns else default
        tax = import_decimal(value('税率（%）', 0), '税率', 0)
        freight = import_decimal(value('运费', 0), '运费', 0)
        category, method = value('成本类别') or '材料费', value('支付方式') or '未记录'
        paid, invoice = value('支付状态') or '未确认', value('票据状态') or '未确认'
        purchaser, purpose, notes = value('经办人'), value('用途'), value('备注')
    else:
        tax, freight = None, Decimal(0)
        category, method = _cell(row, 14) or '材料费', _cell(row, 15) or '未记录'
        paid, invoice = _cell(row, 16) or '未确认', _cell(row, 17) or '未确认'
        purchaser, purpose, notes = _cell(row, 18), _cell(row, 19), _cell(row, 20)
    header = {'order_no': str(row[1]).strip(), 'purchase_type': row[2],
              'project_id': matches[0] if matches else None, 'project_name': project_name,
              'supplier_id': int(row[5]) if _cell(row, 5) else None,
              'merchant_name_snapshot': str(row[7]).strip(), 'purchase_date': str(_cell(row, 3))[:10],
              'payment_method': method, 'payment_status': paid, 'invoice_status': invoice,
              'purchaser': purchaser, 'freight_amount_cents': import_minor(freight), 'notes': notes}
    item = {'product_id': int(row[6]) if _cell(row, 6) else None,
            'material_name_snapshot': str(row[8]).strip(), 'specification_snapshot': _cell(row, 9),
            'unit_snapshot': _cell(row, 10), 'quantity': str(quantity), 'cost_category': category,
            'purpose': purpose, 'notes': notes, **import_settlement_fields(row, columns)}
    if tax is None:
        item.update(unit_price_cents=import_minor(unit_price),
                    line_amount_cents=import_minor(import_decimal(_cell(row, 13, None), '金额', quantity * unit_price)))
    else:
        basis = _cell(row, columns['报价口径']) if '报价口径' in columns else 'exclusive'
        item.update(material_unit_price_cents=import_minor(unit_price), tax_rate_bps=import_minor(tax),
                    price_basis=basis or 'exclusive', tax_inclusive_unit_price_cents=None)
        if basis == 'inclusive':
            if '含税单价' not in columns:
                raise ValueError('含税采购需要含税单价列')
            item['tax_inclusive_unit_price_cents'] = import_minor(import_decimal(
                _cell(row, columns['含税单价'], None), '含税单价'))
    return header, item


def read_import(module, path):
    """Validate every nonempty row before any business mutation is attempted."""
    if module not in ('suppliers', 'offers', 'purchases'):
        raise ValueError('未开放此 Excel 导入')
    with closing(load_workbook(path, data_only=False, read_only=True)) as raw, \
         closing(load_workbook(path, data_only=True, read_only=True)) as cached:
        sheet = raw.active
        if sheet.max_row > 5001 or sheet.max_column > 100:
            raise ValueError('每次最多5000行、100列，请拆分文件')
        headers = [cell.value for cell in sheet[1]]
        rows = []
        pairs = zip(sheet.iter_rows(min_row=2), cached.active.iter_rows(min_row=2, values_only=True))
        for number, (cells, values) in enumerate(pairs, 2):
            if not any(cell.value is not None and str(cell.value).strip() for cell in cells):
                continue
            for cell, value in zip(cells, values):
                if cell.data_type == 'f' and value is None:
                    raise ValueError(f'{sheet.title}!{cell.coordinate} 公式没有已计算值，请在 Excel 中计算并保存')
            rows.append((number, values))
    if not rows:
        raise ValueError('文件没有可导入的业务行')
    projects = defaultdict(list)
    if module == 'purchases':
        for project in project_service.list_projects():
            projects[project['name']].append(project['id'])
    records, orders = [], {}
    columns = {name: index for index, name in enumerate(headers)}
    for number, row in rows:
        try:
            if module != 'purchases':
                data = _supplier(row, headers) if module == 'suppliers' else _offer(row, headers)
                records.append({'line': number, 'data': data, 'item_count': 1})
                continue
            header, item = _purchase(row, columns, projects)
            key = header['order_no']
            if key not in orders:
                record = {'line': number, 'data': header, 'items': [], 'item_count': 0}
                orders[key] = record
                records.append(record)
            record = orders[key]
            saved = record['data']
            for field in ('purchase_type', 'project_id', 'project_name', 'supplier_id',
                          'merchant_name_snapshot', 'purchase_date', 'payment_method',
                          'payment_status', 'invoice_status', 'purchaser'):
                if saved.get(field) != header.get(field):
                    raise ValueError('同一采购单的供应商、项目、日期和付款信息必须一致')
            if record['items']:
                saved['freight_amount_cents'] += header['freight_amount_cents']
            record['items'].append(item)
            record['item_count'] += 1
        except (ValueError, TypeError, IndexError, InvalidOperation) as error:
            raise ValueError(f'第 {number} 行：{error}；整批未提交') from None
    return {'records': records, 'row_count': len(rows)}


def apply_import(module, batch, conn):
    """Caller owns the transaction, so duplicates or failed rows roll back the batch."""
    created = []
    for row in batch['records']:
        data = row['data']
        try:
            if module == 'purchases':
                if conn.execute('SELECT 1 FROM purchase_orders WHERE order_no=?', (data['order_no'],)).fetchone():
                    raise ValueError('采购单号已存在；重复导入不会写入')
                identity = procurement_service.add_purchase_order(data, row['items'])
            elif module == 'suppliers':
                if any(item['name'].strip() == str(data['name']).strip()
                       for item in master_data_service.list_suppliers()):
                    raise ValueError('供应商名称已存在；请使用档案编辑，重复导入不会写入')
                identity = master_data_service.create_supplier(data)
            elif module == 'offers':
                if any(item['supplier_id'] == data['supplier_id'] and item['name'] == data['name']
                       and item['specification'] == data['specification']
                       for item in master_data_service.list_supplier_offers()):
                    raise ValueError('相同供应商、材料和规格报价已存在；请编辑报价')
                identity = master_data_service.create_supplier_offer(data)
            else:
                raise ValueError('未开放此 Excel 导入')
            created.append({'module': module, 'id': identity, 'item_count': row['item_count']})
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(f'第 {row["line"]} 行：{error}；整批未提交') from None
    return created
