import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox, filedialog
from services import (
    contract_service,
    cost_service,
    finance_service,
    master_data_service,
    project_service,
    procurement_service,
)
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from openpyxl import Workbook, load_workbook


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


class ImportExportPage:
    def __init__(self, parent):
        self.parent = parent
        self.build_ui()

    def build_ui(self):
        header = ttk.Frame(self.parent)
        header.pack(fill=X, pady=(0, 16))
        ttk.Label(header, text="数据导入导出", style="PageTitle.TLabel").pack(anchor=W)
        ttk.Label(
            header, text="通过标准 Excel 模板批量维护主数据和采购记录",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(4, 0))

        # 供应商导出导入
        frame1 = ttk.Labelframe(self.parent, text="供应商数据", bootstyle=PRIMARY)
        frame1.pack(fill=X, pady=(0, 10), padx=0, ipady=4)
        ttk.Button(frame1, text="导出供应商到 Excel", bootstyle=INFO, command=self.export_suppliers).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(frame1, text="从 Excel 导入供应商", bootstyle=SUCCESS, command=self.import_suppliers).pack(side=LEFT, padx=10, pady=10)

        # 产品导出导入
        frame2 = ttk.Labelframe(self.parent, text="材料与供应商报价", bootstyle=PRIMARY)
        frame2.pack(fill=X, pady=(0, 10), padx=0, ipady=4)
        ttk.Button(frame2, text="导出产品到 Excel", bootstyle=INFO, command=self.export_products).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(frame2, text="从 Excel 导入产品", bootstyle=SUCCESS, command=self.import_products).pack(side=LEFT, padx=10, pady=10)

        # 采购记录导出导入
        frame3 = ttk.Labelframe(self.parent, text="采购记录", bootstyle=PRIMARY)
        frame3.pack(fill=X, pady=(0, 10), padx=0, ipady=4)
        ttk.Button(frame3, text="导出采购记录到 Excel", bootstyle=INFO, command=self.export_purchases).pack(side=LEFT, padx=10, pady=10)
        ttk.Button(frame3, text="从 Excel 导入采购记录", bootstyle=SUCCESS, command=self.import_purchases).pack(side=LEFT, padx=10, pady=10)

        frame4 = ttk.Labelframe(
            self.parent, text="经营数据归档", bootstyle=PRIMARY
        )
        frame4.pack(fill=X, pady=(0, 10), padx=0, ipady=4)
        ttk.Button(
            frame4,
            text="导出项目经营全量工作簿",
            bootstyle=INFO,
            command=self.export_operating_workbook,
        ).pack(side=LEFT, padx=10, pady=10)
        ttk.Label(
            frame4,
            text="项目、合同分配、结算、开票、回款和成本",
            style="PageSub.TLabel",
        ).pack(side=LEFT, padx=8)
        ttk.Button(frame4, text="备份数据库与附件", bootstyle=INFO,
                   command=self.backup_business_data).pack(side=LEFT, padx=8)

        # 说明
        info = ttk.Label(self.parent, text="说明：采购数据已使用新版统一格式，正式采购需有供应商和产品，零星采购可只填商户与材料。项目名称不存在时，导入会自动建立项目。", wraplength=800, justify=LEFT)
        info.configure(style="PageSub.TLabel")
        info.pack(anchor=W, pady=(8, 0))

    def backup_business_data(self):
        from services.backup_service import create_backup_archive
        path = filedialog.asksaveasfilename(
            defaultextension=".zip", filetypes=[("业务备份", "*.zip")],
            initialfile=f"经营系统备份_{datetime.now():%Y%m%d_%H%M%S}.zip",
        )
        if not path:
            return
        try:
            result = create_backup_archive(path)
        except Exception as error:
            messagebox.showwarning("备份未完成", str(error))
            return
        message = f"已备份数据库及 {len(result['files'])} 个附件。\n{path}"
        if result["missing_files"]:
            messagebox.showwarning("备份完成，部分原文件缺失", message + "\n缺失清单已写入备份包。")
        else:
            messagebox.showinfo("备份完成", message)

    def export_operating_workbook(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
            initialfile=f"项目经营全量_{datetime.now():%Y%m%d}.xlsx",
        )
        if not path:
            return
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
        workbook.save(path)
        messagebox.showinfo("导出成功", f"经营数据已归档到：\n{path}")

    def export_suppliers(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel 文件", "*.xlsx")])
        if not path:
            return
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
        wb.save(path)
        messagebox.showinfo("成功", f"供应商数据已导出到：\n{path}")

    def import_suppliers(self):
        path = filedialog.askopenfilename(filetypes=[("Excel 文件", "*.xlsx")])
        if not path:
            return
        wb = load_workbook(path)
        ws = wb.active
        count = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[1]:
                continue
            data = {
                "name": row[1],
                "category": row[2] if row[2] else "钢材",
                "contact": row[3] if row[3] else "",
                "price_level": row[4] if row[4] else "中",
                "delivery": row[5] if row[5] else "一般",
                "quality": row[6] if row[6] else "良",
                "export": row[7] if row[7] else "否",
                "notes": row[8] if len(row) > 8 and row[8] else "",
                "default_tax_rate_percent": (
                    row[9] if len(row) > 9 and row[9] is not None else 0
                ),
            }
            master_data_service.create_supplier(data)
            count += 1
        messagebox.showinfo("成功", f"成功导入 {count} 条供应商数据")

    def export_products(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel 文件", "*.xlsx")])
        if not path:
            return
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
        wb.save(path)
        messagebox.showinfo("成功", f"产品数据已导出到：\n{path}")

    def import_products(self):
        path = filedialog.askopenfilename(filetypes=[("Excel 文件", "*.xlsx")])
        if not path:
            return
        wb = load_workbook(path)
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        new_format = "税率（%）" in headers
        count = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[3]:
                continue
            try:
                supplier_id = int(row[1]) if row[1] else None
                price = float(row[6]) if row[6] else 0
                tax_rate = (
                    float(row[7]) if new_format and row[7] is not None else None
                )
            except ValueError:
                continue
            if not supplier_id:
                continue
            data = {
                "supplier_id": supplier_id,
                "name": row[3],
                "specification": row[4] if row[4] else "",
                "unit": row[5] if row[5] else "",
                "price": price,
                "notes": (
                    row[9] if new_format and len(row) > 9 and row[9]
                    else row[7] if not new_format and len(row) > 7 and row[7]
                    else ""
                ),
            }
            if tax_rate is not None:
                data["tax_rate_percent"] = tax_rate
            if "报价口径" in headers:
                data["price_basis"] = row[headers.index("报价口径")] or "exclusive"
                if data["price_basis"] == "inclusive":
                    data["price"] = row[headers.index("含税单价")]
            master_data_service.create_supplier_offer(data)
            count += 1
        messagebox.showinfo("成功", f"成功导入 {count} 条产品数据")

    def export_purchases(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel 文件", "*.xlsx")])
        if not path:
            return
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
            ws.append([
                row["id"], row["order_no"], row["purchase_type"], row["purchase_date"],
                row["project_name"] or "", row["supplier_id"], row["product_id"],
                row["merchant_name_snapshot"], row["material_name_snapshot"],
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
        wb.save(path)
        messagebox.showinfo("成功", f"采购记录已导出到：\n{path}")

    def import_purchases(self):
        path = filedialog.askopenfilename(filetypes=[("Excel 文件", "*.xlsx")])
        if not path:
            return
        try:
            wb = load_workbook(path, data_only=True)
        except Exception as error:
            messagebox.showwarning("无法读取采购文件", str(error))
            return
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        is_v2 = "采购类型" in headers and "供应商/商户" in headers
        has_tax_freight = "材料单价（未税）" in headers and "运费" in headers
        column = {name: index for index, name in enumerate(headers)}
        projects = {p["name"]: p["id"] for p in project_service.list_projects()}
        count = 0
        skipped = 0
        errors = []
        pending_orders = {}
        invalid_orders = set()

        def queue_purchase(header, item):
            key = header.get("order_no") or f"row:{row_number}"
            if key not in pending_orders:
                pending_orders[key] = (dict(header), [], row_number)
            saved_header, items, _ = pending_orders[key]
            for field in ("purchase_type", "project_id", "project_name", "supplier_id", "merchant_name_snapshot", "purchase_date", "payment_method", "payment_status", "invoice_status", "purchaser"):
                if saved_header.get(field) != header.get(field):
                    raise ValueError("同一采购单的供应商、项目、日期和付款信息必须一致")
            if items:
                saved_header["freight_amount_cents"] = saved_header.get("freight_amount_cents", 0) + header.get("freight_amount_cents", 0)
            items.append(item)
        for row_number, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(value is not None and str(value).strip() for value in row):
                continue
            try:
                if is_v2:
                    if not row or not row[2] or not row[7] or not row[8]:
                        raise ValueError("采购类型、供应商/商户、材料名称不能为空")
                    project_name = str(row[4]).strip() if row[4] else ""
                    if row[2] not in ("正式采购", "零星采购"):
                        raise ValueError("采购类型只能为正式采购或零星采购")
                    purchase_type = row[2]
                    supplier_id = int(row[5]) if row[5] else None
                    product_id = int(row[6]) if row[6] else None
                    quantity = import_decimal(row[11], "数量")
                    unit_price = import_decimal(row[12], "单价")
                    amount = quantity * unit_price if has_tax_freight else import_decimal(row[13], "金额", quantity * unit_price)
                    if has_tax_freight:
                        tax_rate = import_decimal(row[column["税率（%）"]], "税率", 0)
                        freight = import_decimal(row[column["运费"]], "运费", 0)
                        cost_category = row[column["成本类别"]] or "材料费"
                        payment_method = row[column["支付方式"]] or "未记录"
                        payment_status = row[column["支付状态"]] or "未确认"
                        invoice_status = row[column["票据状态"]] or "未确认"
                        purchaser = row[column["经办人"]] or ""
                        purpose = row[column["用途"]] or ""
                        notes = row[column["备注"]] or ""
                    else:
                        tax_rate = None
                        freight = Decimal(0)
                        cost_category = row[14] or "材料费"
                        payment_method = row[15] or "未记录"
                        payment_status = row[16] or "未确认"
                        invoice_status = row[17] or "未确认"
                        purchaser = row[18] or ""
                        purpose = row[19] or ""
                        notes = row[20] or ""
                    queue_purchase({
                        "order_no": str(row[1]).strip() if row[1] else None,
                        "purchase_type": purchase_type,
                        "project_id": projects.get(project_name),
                        "project_name": project_name,
                        "supplier_id": supplier_id,
                        "merchant_name_snapshot": str(row[7]).strip(),
                        "purchase_date": str(row[3])[:10],
                        "payment_method": payment_method,
                        "payment_status": payment_status,
                        "invoice_status": invoice_status,
                        "purchaser": purchaser,
                        "freight_amount_cents": import_minor(freight),
                        "notes": notes,
                    }, {
                        "product_id": product_id, "material_name_snapshot": str(row[8]).strip(),
                        "specification_snapshot": row[9] or "", "unit_snapshot": row[10] or "",
                        "quantity": str(quantity),
                        "cost_category": cost_category,
                        **import_settlement_fields(row, column),
                        "purpose": purpose, "notes": notes,
                        **(
                            {
                                "material_unit_price_cents": import_minor(unit_price),
                                "tax_rate_bps": import_minor(tax_rate),
                                "price_basis": (row[column["报价口径"]] or "exclusive") if "报价口径" in column else "exclusive",
                                "tax_inclusive_unit_price_cents": import_minor(import_decimal(row[column["含税单价"]], "含税单价")) if "报价口径" in column and row[column["报价口径"]] == "inclusive" else None,
                            }
                            if tax_rate is not None
                            else {
                                "unit_price_cents": import_minor(unit_price),
                                "line_amount_cents": import_minor(amount),
                            }
                        ),
                    })
                else:
                    # 兼容旧版 12 列模板，导入后直接进入新版正式采购。
                    if not row or not row[2] or not row[3]:
                        raise ValueError("旧模板的供应商和产品编号不能为空")
                    supplier_id, product_id = int(row[2]), int(row[3])
                    supplier = master_data_service.get_supplier_by_legacy_id(supplier_id)
                    product = master_data_service.get_supplier_offer_by_legacy_id(product_id)
                    if not supplier or not product:
                        raise ValueError("找不到旧模板对应的供应商或产品")
                    project_name = str(row[10]).strip() if len(row) > 10 and row[10] else ""
                    quantity = import_decimal(row[7], "数量")
                    unit_price = import_decimal(row[8], "单价")
                    amount = import_decimal(row[9], "金额", quantity * unit_price)
                    queue_purchase({
                        "purchase_type": "正式采购", "project_id": projects.get(project_name),
                        "project_name": project_name,
                        "supplier_id": supplier["id"], "merchant_name_snapshot": supplier["name"],
                        "purchase_date": str(row[1])[:10], "notes": row[11] if len(row) > 11 and row[11] else "",
                    }, {
                        "product_id": product["id"], "material_name_snapshot": product["name"],
                        "specification_snapshot": product["specification"], "unit_snapshot": product["unit"],
                        "quantity": str(quantity), "unit_price_cents": import_minor(unit_price),
                        "line_amount_cents": import_minor(amount), "cost_category": "材料费",
                    })
            except Exception as error:
                skipped += 1
                errors.append((row_number, str(error)))
                if is_v2 and len(row) > 1 and row[1]:
                    invalid_orders.add(str(row[1]).strip())
        for key, (header, items, first_row) in pending_orders.items():
            if key in invalid_orders:
                errors.append((first_row, "同单存在错误，本单全部未保存"))
                continue
            try:
                procurement_service.add_purchase_order(header, items)
                count += 1
            except Exception as error:
                skipped += 1
                errors.append((first_row, str(error)))
        wb.close()
        if errors:
            dialog = ttk.Toplevel(self.parent)
            dialog.title("采购导入结果")
            from ui.dialogs import build_form_dialog, add_form_actions
            body, footer = build_form_dialog(dialog, self.parent, 760, 500, min_width=600, min_height=400)
            ttk.Label(body, text=f"成功 {count} 条，失败 {skipped} 条。已成功行不要重复导入。失败行未保存。", wraplength=660).pack(anchor=W)
            details = ttk.Text(body, wrap="word", height=16)
            details.pack(fill=BOTH, expand=True, pady=8)
            details.insert("1.0", "\n".join(f"第 {number} 行：{reason}" for number, reason in errors))
            details.configure(state="disabled")
            add_form_actions(footer, cancel_command=dialog.destroy, primary_text="关闭", primary_command=dialog.destroy)
        else:
            messagebox.showinfo("导入完成", f"成功导入 {count} 条。")


