"""PurchaseOrderDialogMixin: extracted UI behavior from pages/purchase_management_page.py.

The dialog is split into focused mixin methods (form build, calculation,
allocation, product typeahead, batch staging, save). Reusable pure logic
lives in the module-level functions below so unit tests can exercise it
without a Tk root.
"""

from datetime import datetime
from tkinter import messagebox
from types import SimpleNamespace

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import master_data_service, procurement_service
from ui.components import DatePicker
from ui.dialogs import add_form_actions, build_form_dialog
from ui.error_handling import show_unexpected_error
from ui.purchase_entry import reset_continuous_purchase_line
from ui.scaling import scale_px
from ui.typeahead import filter_supplier_offer_labels
from ui.attachments import open_attachment_manager
from pages.purchase_order_product import PurchaseOrderProductMixin


def initial_form_values(is_petty, editing, edit_data, project_value, supplier_value):
    """Initial raw values for every form variable of the purchase dialog."""
    return {
        "settlement_mode": procurement_service.SETTLEMENT_MODES[edit_data.get("settlement_mode", "quantity")],
        "net_weight": edit_data.get("net_weight") or "",
        "weight_unit": edit_data.get("weight_unit") or "吨",
        "weight_unit_price": edit_data.get("weight_unit_price") or "",
        "settlement_total": f"{edit_data['settlement_total_cents'] / 100:.2f}" if edit_data.get("settlement_total_cents") is not None else "",
        "weigh_ticket_no": edit_data.get("weigh_ticket_no") or "",
        "price_basis": "含税价" if edit_data.get("price_basis", "inclusive") == "inclusive" else "未税价",
        "project": project_value,
        "date": edit_data.get("purchase_date", datetime.now().strftime("%Y-%m-%d")),
        "supplier": supplier_value,
        "merchant": edit_data.get("merchant_name_snapshot", ""),
        "material": edit_data.get("material_name_snapshot", ""),
        "spec": edit_data.get("specification_snapshot", ""),
        "unit": edit_data.get("unit_snapshot", "件"),
        "qty": str(edit_data.get("quantity", "1")),
        "material_unit_price": (
            str(edit_data.get("tax_inclusive_unit_price_cents" if edit_data.get("price_basis") == "inclusive" else "material_unit_price_cents", 0) / 100)
            if editing else ""
        ),
        "tax_rate": (
            f"{edit_data.get('tax_rate_bps', 0) / 100:g}" if editing else "0"
        ),
        "tax_inclusive_unit_price": (
            f"{edit_data.get('tax_inclusive_unit_price_cents', 0) / 100:.2f}"
            if editing else "0.00"
        ),
        "material_amount": (
            f"{edit_data.get('material_amount_cents', 0) / 100:.2f}"
            if editing else "0.00"
        ),
        "material_gross": "0.00",
        "current_material_gross": "--",
        "tax_amount": (
            f"{edit_data.get('tax_amount_cents', 0) / 100:.2f}"
            if editing else "0.00"
        ),
        "freight": (
            f"{edit_data.get('freight_amount_cents', 0) / 100:.2f}"
            if editing else "0.00"
        ),
        "project_cost": (
            f"{edit_data.get('project_cost_cents', 0) / 100:.2f}"
            if editing else "0.00"
        ),
        "category": edit_data.get("cost_category", "材料费"),
        "purpose": edit_data.get("purpose", ""),
        "payment_method": edit_data.get("payment_method", "微信" if is_petty else "对公转账"),
        "payment_status": edit_data.get("payment_status", "已付款" if is_petty else "未确认"),
        "invoice": edit_data.get("invoice_status", "无发票" if is_petty else "未确认"),
        "purchaser": edit_data.get("purchaser", ""),
        "notes": edit_data.get("notes", ""),
        "product": "",
        "attribution": (
            "多项目平均分摊"
            if edit_data.get("allocation_method") == "equal"
            else "单项目归集"
        ),
    }


def build_pricing_item(values):
    """Assemble the pricing item payload from raw form strings."""
    mode = next(key for key, label in procurement_service.SETTLEMENT_MODES.items() if label == values["settlement_mode"])
    item = {
        "settlement_mode": mode, "quantity": values["qty"],
        "price_basis": "inclusive" if values["price_basis"] == "含税价" else "exclusive",
        "tax_rate_bps": procurement_service.decimal_minor(values["tax_rate"]),
        "net_weight": values["net_weight"], "weight_unit": values["weight_unit"],
        "weight_unit_price": values["weight_unit_price"],
        "weigh_ticket_no": values["weigh_ticket_no"],
    }
    if mode == "quantity":
        price = procurement_service.decimal_minor(values["material_unit_price"])
        item.update(material_unit_price_cents=price, tax_inclusive_unit_price_cents=price)
    elif mode == "total":
        item["settlement_total_cents"] = procurement_service.decimal_minor(values["settlement_total"])
    return item


def queued_line_total_cents(pending_items):
    """Tax-inclusive line total of the staged batch items."""
    return sum(
        procurement_service.calculate_purchase_item_amounts({}, item)["line_amount_cents"]
        for item in pending_items
    )


def format_purchase_amounts(amounts, queued_cents):
    """Display strings for the dialog summary fields."""
    return {
        "current_material_gross": f"¥{amounts['line_amount_cents'] / 100:,.2f}",
        "tax_inclusive_unit_price": f"{amounts['tax_inclusive_unit_price_cents'] / 100:.2f}",
        "material_amount": f"{amounts['material_amount_cents'] / 100:.2f}",
        "material_gross": f"{(amounts['line_amount_cents'] + queued_cents) / 100:.2f}",
        "tax_amount": f"{amounts['tax_amount_cents'] / 100:.2f}",
        "project_cost": f"{(amounts['project_cost_cents'] + queued_cents) / 100:.2f}",
    }


def allocation_preview_text(attribution, project_cost_text, project_ids, project_labels, money):
    """Allocation preview text for the current selection, or a hint."""
    if attribution != "多项目平均分摊":
        return ""
    if len(project_ids) < 2:
        return "请至少勾选两个项目，金额会自动平均分摊。"
    try:
        total_cents = round(float(project_cost_text) * 100)
        plan = procurement_service.build_equal_allocation_plan(total_cents, project_ids)
    except (TypeError, ValueError):
        return "填写数量和价格后，这里会显示分摊结果。"
    return "分摊预览：\n" + "\n".join(
        f"{project_labels[line['project_id']]}  {money(line['amount_minor'])}"
        for line in plan
    )


def resolve_supplier_product(is_petty, *, merchant_entry, material_entry,
                             supplier_entry, supplier_map, product_entry,
                             products_by_label, settlement_mode):
    """Resolve form text to (supplier_id, product_id, merchant, material)."""
    if is_petty:
        return None, None, merchant_entry.strip(), material_entry.strip()
    supplier_id = supplier_map.get(supplier_entry)
    product = products_by_label.get(product_entry)
    product_id = product["id"] if product else None
    material = material_entry.strip()
    if settlement_mode != "quantity" and not product_id:
        material = product_entry.strip()
    return supplier_id, product_id, supplier_entry.strip(), material


def resolve_allocation_method(use_equal_allocation, selected_project_id):
    if use_equal_allocation:
        return "equal"
    return "direct" if selected_project_id else "unassigned"


def validate_purchase_submission(*, has_current, is_petty, merchant, supplier_id,
                                 product_id, settlement_mode, material,
                                 use_equal_allocation, allocation_project_count,
                                 project_selected):
    """First blocking validation error as (title, message, focus), or None."""
    if not merchant or (not is_petty and not supplier_id):
        return "提示", "请选择供应商或填写商户名称", None
    if has_current and not is_petty and not product_id and settlement_mode == "quantity":
        return (
            "提示",
            "输入材料名称或规格后，请从匹配结果中选择材料；没有结果时请先维护材料报价。",
            "product",
        )
    if has_current and not material:
        return "提示", "请完整填写供应商/商户和材料信息", None
    if use_equal_allocation and allocation_project_count < 2:
        return "提示", "工具和设备多项目平均分摊至少需要勾选两个项目。", None
    if not use_equal_allocation and not project_selected:
        return (
            "请选择项目",
            "请从匹配列表中选择项目；暂时不归属项目时请选择“待归集（稍后分配）”。",
            "project",
        )
    return None


def build_order_header(purchase_type, *, project_id, project_ids, allocation_method,
                       supplier_id, merchant, purchase_date, payment_method,
                       payment_status, invoice_status, purchaser, freight_text, notes):
    """Order header payload; freight is parsed to integer cents."""
    return {
        "purchase_type": purchase_type,
        "project_id": project_id,
        "project_ids": project_ids,
        "allocation_method": allocation_method,
        "supplier_id": supplier_id,
        "merchant_name_snapshot": merchant,
        "purchase_date": purchase_date,
        "payment_method": payment_method,
        "payment_status": payment_status,
        "invoice_status": invoice_status,
        "purchaser": purchaser,
        "freight_amount_cents": procurement_service.decimal_minor(freight_text or 0),
        "notes": notes,
    }


def build_order_item(*, item_id, product_id, material, spec, unit, category,
                     pricing, purpose, notes):
    """Order item payload merging the validated pricing dict."""
    return {
        "item_id": item_id,
        "product_id": product_id,
        "material_name_snapshot": material,
        "specification_snapshot": spec,
        "unit_snapshot": unit,
        "cost_category": category,
        **pricing,
        "purpose": purpose,
        "notes": notes,
    }


class PurchaseOrderDialogMixin(PurchaseOrderProductMixin):
    def open_purchase_dialog(self, purchase_type, order_id=None):
        ctx = self._prepare_purchase_dialog(purchase_type, order_id)
        if ctx is None:
            return
        self._build_purchase_form(ctx)
        self._build_purchase_summary(ctx)
        self._setup_purchase_layout(ctx)
        self._setup_purchase_calculation(ctx)
        if not ctx.is_petty:
            self._setup_product_typeahead(ctx)
        self._calculate(ctx)
        self._build_batch_panel(ctx)
        self._add_purchase_actions(ctx)

    def _prepare_purchase_dialog(self, purchase_type, order_id):
        is_petty = purchase_type == "零星采购"
        edit_data = procurement_service.get_purchase_order(order_id) if order_id else {}
        if order_id and not edit_data:
            messagebox.showwarning("提示", "采购单不存在或已经作废")
            return None
        saved_items = edit_data.get("items", [])
        if saved_items:
            edit_data = dict(edit_data, **{key: value for key, value in saved_items[0].items() if key != "items"})
        # 已完工项目仍可能补录材料、运费或迟到票据；仅关闭项目停止新增业务。
        projects = self._purchase_projects(include_closed=bool(order_id))
        project_map = {"待归集（稍后分配）": None}
        project_map.update({f"{p['project_code']} · {p['name']}": p["id"] for p in projects})
        suppliers = master_data_service.list_suppliers()
        supplier_map = {f"{s['name']}": s["id"] for s in suppliers}

        dialog = ttk.Toplevel(self.parent)
        if order_id:
            dialog.title("修改零星采购" if is_petty else "修改正式采购")
        else:
            dialog.title("快速记零星采购" if is_petty else "新增正式采购")
        body, footer = build_form_dialog(
            dialog, self.parent, 1040, 730,
            min_width=560, min_height=480,
        )

        project_value = next(iter(project_map))
        for label, project_id in project_map.items():
            if project_id == edit_data.get("project_id"):
                project_value = label
                break
        supplier_value = next(iter(supplier_map), "")
        for label, supplier_id in supplier_map.items():
            if supplier_id == edit_data.get("supplier_id"):
                supplier_value = label
                break

        values = initial_form_values(is_petty, bool(order_id), edit_data, project_value, supplier_value)
        vars_ = {key: ttk.StringVar(value=value) for key, value in values.items()}
        ctx = SimpleNamespace(
            purchase_type=purchase_type,
            order_id=order_id,
            is_petty=is_petty,
            edit_data=edit_data,
            pending_items=[dict(item) for item in saved_items[1:]],
            current_item_id=saved_items[0]["item_id"] if saved_items else None,
            projects=projects,
            project_map=project_map,
            supplier_map=supplier_map,
            products_by_label={},
            dialog=dialog,
            body=body,
            footer=footer,
            vars_=vars_,
            product_hint_var=ttk.StringVar(),
            continuous_feedback_var=ttk.StringVar(),
            allocation_preview_var=ttk.StringVar(),
            continuous_saved_count=0,
            row=0,
            form_fields=[],
            layout_width=None,
        )
        ctx.selected_project_vars = {
            project["id"]: ttk.BooleanVar(value=False) for project in projects
        }
        edit_allocations = (
            procurement_service.get_purchase_allocations(order_id)
            if order_id
            else []
        )
        for allocation in edit_allocations:
            selected = ctx.selected_project_vars.get(allocation["project_id"])
            if selected is not None:
                selected.set(True)

        if not order_id and not is_petty:
            ttk.Label(
                footer,
                textvariable=ctx.continuous_feedback_var,
                bootstyle=SUCCESS,
                wraplength=450,
            ).pack(side=TOP, anchor=W)
        return ctx

    def _add_form_field(self, ctx, label, widget):
        label_widget = ttk.Label(ctx.body, text=label, wraplength=scale_px(ctx.body, 155), justify=LEFT)
        label_widget.grid(row=ctx.row, column=0, sticky=E, padx=(0, 12), pady=6)
        grid_options = {"row": ctx.row, "column": 1, "sticky": EW, "pady": 6}
        if not isinstance(widget, DatePicker):
            grid_options["ipady"] = 4
        widget.grid(**grid_options)
        ctx.row += 1
        ctx.form_fields.append((label_widget, widget))
        return label_widget, widget

    def _build_purchase_form(self, ctx):
        body = ctx.body
        vars_ = ctx.vars_
        category_values = list(procurement_service.PURCHASE_COST_CATEGORIES)
        if vars_["category"].get() not in category_values:
            category_values.append(vars_["category"].get())
        ctx.category_combo = ttk.Combobox(
            body,
            textvariable=vars_["category"],
            values=category_values,
            state="readonly",
        )
        self._add_form_field(ctx, "成本类别", ctx.category_combo)
        ctx.attribution_combo = ttk.Combobox(
            body,
            textvariable=vars_["attribution"],
            values=("单项目归集", "多项目平均分摊"),
            state="readonly",
        )
        ctx.attribution_label, ctx.attribution_combo = self._add_form_field(
            ctx, "项目归集方式", ctx.attribution_combo
        )
        ctx.project_combo = ttk.Combobox(
            body,
            textvariable=vars_["project"],
            values=list(ctx.project_map),
            state="normal",
        )
        ctx.project_label, ctx.project_combo = self._add_form_field(ctx, "所属项目（输入关键词）", ctx.project_combo)
        ctx.project_combo.bind("<KeyRelease>", lambda event: self._filter_projects(ctx, event))
        ctx.project_combo.configure(postcommand=lambda: self._filter_projects(ctx))
        self._build_allocation_frame(ctx)
        self._add_form_field(
            ctx,
            "采购日期 *",
            DatePicker(
                body,
                textvariable=vars_["date"],
                popup_title="选择采购日期",
            ),
        )
        if ctx.is_petty:
            self._add_form_field(ctx, "商户名称 *", ttk.Entry(body, textvariable=vars_["merchant"]))
            self._add_form_field(ctx, "材料名称 *", ttk.Entry(body, textvariable=vars_["material"]))
        else:
            ctx.supplier_combo = ttk.Combobox(body, textvariable=vars_["supplier"], values=list(ctx.supplier_map), state="readonly")
            self._add_form_field(ctx, "供应商 *", ctx.supplier_combo)
            ctx.product_combo = ttk.Combobox(body, textvariable=vars_["product"], state="normal")
            self._add_form_field(ctx, "材料 *", ctx.product_combo)
            ctx.product_hint_var.set("输入材料名称或规格即可筛选；请从匹配结果中选择。")
            ttk.Label(
                body,
                textvariable=ctx.product_hint_var,
                style="PageSub.TLabel",
            ).grid(row=ctx.row, column=1, sticky=W, pady=(0, 4))
            ctx.row += 1
        self._add_form_field(ctx, "规格", ttk.Entry(body, textvariable=vars_["spec"]))
        mode_combo = ttk.Combobox(body, textvariable=vars_["settlement_mode"], values=tuple(procurement_service.SETTLEMENT_MODES.values()), state="readonly")
        self._add_form_field(ctx, "计价方式", mode_combo)
        ttk.Label(body, text="过磅填本次净重和重量单价；整批可直接填材料名称，规格清单及磅单在保存后通过“磅单 / 附件”上传。",
                  wraplength=480, style="CardText.TLabel").grid(row=ctx.row, column=1, sticky=W, pady=4)
        ctx.row += 1
        unit_row = ttk.Frame(body)
        ttk.Entry(unit_row, textvariable=vars_["qty"], width=12).pack(side=LEFT)
        ttk.Label(unit_row, text="  单位  ").pack(side=LEFT)
        ttk.Entry(unit_row, textvariable=vars_["unit"], width=10).pack(side=LEFT)
        ctx.qty_fields = self._add_form_field(ctx, "数量 / 单位 *", unit_row)
        ctx.weight_fields = []
        ctx.weight_fields.extend(self._add_form_field(ctx, "实际净重 *", ttk.Entry(body, textvariable=vars_["net_weight"])))
        ctx.weight_fields.extend(self._add_form_field(ctx, "过磅单位", ttk.Combobox(body, textvariable=vars_["weight_unit"], values=("吨", "公斤"), state="readonly")))
        ctx.weight_price_label, weight_price_entry = self._add_form_field(ctx, "重量单价（元/吨）*", ttk.Entry(body, textvariable=vars_["weight_unit_price"]))
        ctx.weight_fields.extend((ctx.weight_price_label, weight_price_entry))
        ctx.total_fields = self._add_form_field(ctx, "整批结算金额（不含运费）*", ttk.Entry(body, textvariable=vars_["settlement_total"]))
        self._add_form_field(ctx, "磅单号 / 结算单号", ttk.Entry(body, textvariable=vars_["weigh_ticket_no"]))
        basis_combo = ttk.Combobox(body, textvariable=vars_["price_basis"], values=("含税价", "未税价"), state="readonly")
        self._add_form_field(ctx, "报价口径", basis_combo)
        ctx.price_label, ctx.price_entry = self._add_form_field(ctx, "材料单价（含税，元）*", ttk.Entry(body, textvariable=vars_["material_unit_price"]))
        self._add_form_field(ctx, "税率（%）*", ttk.Entry(body, textvariable=vars_["tax_rate"]))
        ctx.gross_label, ctx.gross_entry = self._add_form_field(
            ctx,
            "含税单价（元）",
            ttk.Entry(body, textvariable=vars_["tax_inclusive_unit_price"], state="readonly"),
        )
        self._add_form_field(ctx, "运费（元）", ttk.Entry(body, textvariable=vars_["freight"]))
        self._add_form_field(ctx, "用途 / 施工位置", ttk.Entry(body, textvariable=vars_["purpose"]))
        self._add_form_field(ctx, "支付方式", ttk.Combobox(body, textvariable=vars_["payment_method"],
                                          values=["现金", "微信", "支付宝", "对公转账", "员工垫付", "未记录"], state="readonly"))
        self._add_form_field(ctx, "支付状态", ttk.Combobox(body, textvariable=vars_["payment_status"],
                                          values=["已付款", "未付款", "未确认"], state="readonly"))
        self._add_form_field(ctx, "票据状态", ttk.Combobox(body, textvariable=vars_["invoice"],
                                          values=["有发票", "收据", "无发票", "未确认"], state="readonly"))
        self._add_form_field(ctx, "经办人", ttk.Entry(body, textvariable=vars_["purchaser"]))
        self._add_form_field(ctx, "备注", ttk.Entry(body, textvariable=vars_["notes"]))
        body.columnconfigure(1, weight=1)

    def _build_allocation_frame(self, ctx):
        allocation_frame = ttk.Frame(ctx.body, style="Card.TFrame", padding=12)
        allocation_header = ttk.Frame(allocation_frame, style="Card.TFrame")
        allocation_header.pack(fill=X, pady=(0, 6))
        ttk.Label(
            allocation_header,
            text="选择共同承担成本的项目",
            style="CardTitle.TLabel",
        ).pack(side=LEFT)
        ctx.select_all_projects_button = ttk.Button(
            allocation_header,
            text="全选项目",
            bootstyle="secondary-outline",
            command=lambda: self._toggle_all_allocation_projects(ctx),
        )
        ctx.select_all_projects_button.pack(side=RIGHT)
        ctx.project_labels = {
            project["id"]: f"{project['project_code']} · {project['name']}"
            for project in ctx.projects
        }
        ttk.Separator(allocation_frame).pack(fill=X, pady=(0, 4))
        for project in ctx.projects:
            ttk.Checkbutton(
                allocation_frame,
                text=ctx.project_labels[project["id"]],
                variable=ctx.selected_project_vars[project["id"]],
                command=lambda: self._allocation_project_selection_changed(ctx),
            ).pack(anchor=W, pady=2)
        ttk.Separator(allocation_frame).pack(fill=X, pady=(8, 6))
        ttk.Label(
            allocation_frame,
            textvariable=ctx.allocation_preview_var,
            style="CardText.TLabel",
            wraplength=500,
            justify=LEFT,
        ).pack(anchor=W)
        allocation_frame.grid(
            row=ctx.row, column=0, columnspan=2, sticky=EW, pady=(2, 8)
        )
        ctx.row += 1
        ctx.allocation_frame = allocation_frame

    def _filter_projects(self, ctx, event=None):
        vars_ = ctx.vars_
        if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        query = vars_["project"].get().strip().casefold()
        if vars_["project"].get() in ctx.project_map:
            labels = list(ctx.project_map)
        else:
            labels = [label for label in ctx.project_map if query in label.casefold()]
        ctx.project_combo.configure(values=labels)
        if event and query and labels and vars_["project"].get() not in ctx.project_map:
            ctx.project_combo.after_idle(lambda: ctx.project_combo.event_generate("<Down>"))

    def _build_purchase_summary(self, ctx):
        footer = ctx.footer
        vars_ = ctx.vars_
        current_summary = ttk.Frame(footer)
        current_summary.pack(side=TOP, fill=X, pady=(0, 6))
        ttk.Label(current_summary, text="当前材料金额（含税，不含运费）：", style="CardTitle.TLabel").pack(side=LEFT)
        ttk.Label(current_summary, textvariable=vars_["current_material_gross"], style="CardTitle.TLabel").pack(side=LEFT)
        summary = ttk.Frame(footer)
        summary.pack(side=TOP, fill=X, pady=(0, 10))
        for index, (label, key) in enumerate((
            ("整单材料合计 · 含税（元）", "material_gross"),
            ("另列运费（元）", "freight"),
            ("项目成本 · 含运费（元）", "project_cost"),
        )):
            column = ttk.Frame(summary)
            column.grid(row=0, column=index, sticky=EW, padx=(0, 12))
            ttk.Label(column, text=label).pack(anchor=W)
            ttk.Entry(column, textvariable=vars_[key], state="readonly", width=16).pack(fill=X)
            summary.columnconfigure(index, weight=1)

    def _setup_purchase_layout(self, ctx):
        body = ctx.body
        paired = {widget for pair in ctx.form_fields for widget in pair}
        full_width = [widget for widget in body.winfo_children() if widget not in paired and widget.grid_info()]
        ctx.ordered = sorted(
            [(int(label.grid_info()["row"]), (label, widget)) for label, widget in ctx.form_fields]
            + [(int(widget.grid_info()["row"]), (widget,)) for widget in full_width],
            key=lambda item: item[0],
        )
        body.bind("<Configure>", lambda event: self._layout_purchase_form(ctx, event), add="+")
        ctx.dialog.after_idle(lambda: self._layout_purchase_form(ctx))

    def _layout_purchase_form(self, ctx, event=None):
        body = ctx.body
        width = body.winfo_width()
        if event is not None and width == ctx.layout_width:
            return
        ctx.layout_width = width
        pairs_per_row = 2 if width >= scale_px(body, 850) else 1
        body.columnconfigure(3, weight=1 if pairs_per_row == 2 else 0)
        current_row, slot = 0, 0
        for _, widgets in ctx.ordered:
            if not widgets[0].grid_info():
                continue
            if len(widgets) == 1:
                current_row += bool(slot)
                widgets[0].grid_configure(row=current_row, column=0, columnspan=pairs_per_row * 2, sticky=EW)
                if isinstance(widgets[0], ttk.Label):
                    widgets[0].configure(wraplength=max(300, width - 60))
                current_row += 1
                slot = 0
                continue
            label, widget = widgets
            label.grid_configure(row=current_row, column=slot * 2, sticky=W, padx=(0 if slot == 0 else 20, 12))
            widget.grid_configure(row=current_row, column=slot * 2 + 1, sticky=W if isinstance(widget, DatePicker) else EW)
            slot += 1
            if slot == pairs_per_row:
                current_row += 1
                slot = 0

    @staticmethod
    def _pricing_from_vars(vars_):
        return build_pricing_item({key: var.get() for key, var in vars_.items()})

    def _calculate(self, ctx, *_args):
        vars_ = ctx.vars_
        inclusive = vars_["price_basis"].get() == "含税价"
        mode = vars_["settlement_mode"].get()
        if not ctx.is_petty and mode != "按数量":
            ctx.product_hint_var.set("可选择已有材料或直接填写整批名称；本次重量单价或结算金额请单独填写。")
        for widgets, visible in ((ctx.qty_fields, mode == "按数量"), ((ctx.price_label, ctx.price_entry, ctx.gross_label, ctx.gross_entry), mode == "按数量"), (ctx.weight_fields, mode == "按过磅重量"), (ctx.total_fields, mode == "按结算总额")):
            for widget in widgets:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()
        ctx.weight_price_label.configure(text=f"重量单价（{'含税' if inclusive else '未税'}，元/{vars_['weight_unit'].get()}）*")
        ctx.price_label.configure(text="材料单价（含税，元）*" if inclusive else "材料单价（未税，元）*")
        ctx.gross_label.configure(text="含税成交单价（元）" if inclusive else "含税单价（约，元）")
        try:
            amounts = procurement_service.calculate_purchase_item_amounts(
                {"freight_amount_cents": procurement_service.decimal_minor(vars_["freight"].get() or 0)}, self._pricing_from_vars(vars_))
            queued_amount = queued_line_total_cents(ctx.pending_items)
            for key, text in format_purchase_amounts(amounts, queued_amount).items():
                vars_[key].set(text)
        except (ValueError, TypeError):
            for key in (
                "current_material_gross",
                "tax_inclusive_unit_price",
                "material_amount",
                "material_gross",
                "tax_amount",
                "project_cost",
            ):
                vars_[key].set("--")
        self._refresh_allocation_preview(ctx)
        self._layout_purchase_form(ctx)

    def _selected_allocation_project_ids(self, ctx):
        return [
            project_id
            for project_id, selected in ctx.selected_project_vars.items()
            if selected.get()
        ]

    def _all_allocation_projects_selected(self, ctx):
        return bool(ctx.selected_project_vars) and all(
            selected.get() for selected in ctx.selected_project_vars.values()
        )

    def _sync_select_all_projects(self, ctx):
        ctx.select_all_projects_button.configure(
            text=(
                "取消全选"
                if self._all_allocation_projects_selected(ctx)
                else "全选项目"
            )
        )

    def _toggle_all_allocation_projects(self, ctx):
        select_all = not self._all_allocation_projects_selected(ctx)
        for selected in ctx.selected_project_vars.values():
            selected.set(select_all)
        self._sync_select_all_projects(ctx)
        self._refresh_allocation_preview(ctx)

    def _allocation_project_selection_changed(self, ctx):
        self._sync_select_all_projects(ctx)
        self._refresh_allocation_preview(ctx)

    def _refresh_allocation_preview(self, ctx):
        ctx.allocation_preview_var.set(
            allocation_preview_text(
                ctx.vars_["attribution"].get(),
                ctx.vars_["project_cost"].get(),
                self._selected_allocation_project_ids(ctx),
                ctx.project_labels,
                self.money,
            )
        )

    def _refresh_attribution_ui(self, ctx, *_args):
        vars_ = ctx.vars_
        is_tool = (
            vars_["category"].get()
            == procurement_service.TOOL_EQUIPMENT_CATEGORY
        )
        if not is_tool:
            vars_["attribution"].set("单项目归集")
            ctx.attribution_label.grid_remove()
            ctx.attribution_combo.grid_remove()
        else:
            ctx.attribution_label.grid()
            ctx.attribution_combo.grid()

        if is_tool and vars_["attribution"].get() == "多项目平均分摊":
            ctx.project_label.grid_remove()
            ctx.project_combo.grid_remove()
            ctx.allocation_frame.grid()
        else:
            ctx.project_label.grid()
            ctx.project_combo.grid()
            ctx.allocation_frame.grid_remove()
        self._sync_select_all_projects(ctx)
        self._refresh_allocation_preview(ctx)
        self._layout_purchase_form(ctx)

    def _setup_purchase_calculation(self, ctx):
        vars_ = ctx.vars_
        for key in ("qty", "price_basis", "material_unit_price", "tax_rate", "freight",
                    "settlement_mode", "net_weight", "weight_unit", "weight_unit_price", "settlement_total"):
            vars_[key].trace_add("write", lambda *args: self._calculate(ctx))
        ctx.category_combo.bind("<<ComboboxSelected>>", lambda *args: self._refresh_attribution_ui(ctx))
        ctx.attribution_combo.bind("<<ComboboxSelected>>", lambda *args: self._refresh_attribution_ui(ctx))
        self._refresh_attribution_ui(ctx)

    def _prepare_next_form(self, ctx, saved_material):
        vars_ = ctx.vars_
        ctx.continuous_saved_count += 1
        reset_continuous_purchase_line(vars_)
        for key in ("net_weight", "weight_unit_price", "settlement_total", "weigh_ticket_no"):
            vars_[key].set("")
        ctx.product_combo["values"] = filter_supplier_offer_labels(
            ctx.products_by_label, ""
        )
        ctx.product_hint_var.set(
            f"当前供应商有 {len(ctx.products_by_label)} 条报价；继续输入下一种材料。"
        )
        ctx.continuous_feedback_var.set(
            f"已保存 {ctx.continuous_saved_count} 条，本次：{saved_material}"
        )
        ctx.body.yview_moveto(0)
        ctx.dialog.after_idle(ctx.product_combo.focus_set)

    def _refresh_batch(self, ctx):
        vars_ = ctx.vars_
        ctx.batch_tree.delete(*ctx.batch_tree.get_children())
        total = 0
        for index, item in enumerate(ctx.pending_items):
            amount = procurement_service.calculate_purchase_item_amounts({}, item)["line_amount_cents"]
            total += amount
            ctx.batch_tree.insert("", END, iid=str(index), values=(item["material_name_snapshot"], item.get("quantity", 1), self.money(amount)))
        ctx.continuous_feedback_var.set(f"本单已加入 {len(ctx.pending_items)} 条，含税材料合计 {self.money(total)} 元；运费整单只计一次。")
        if not vars_["material"].get().strip() and not vars_["product"].get().strip():
            vars_["material_gross"].set(f"{total / 100:.2f}")
            try:
                freight = procurement_service.decimal_minor(vars_["freight"].get() or 0)
                vars_["project_cost"].set(f"{(total + freight) / 100:.2f}")
            except ValueError:
                vars_["project_cost"].set("--")

    def _clear_material(self, ctx):
        vars_ = ctx.vars_
        ctx.current_item_id = None
        for key in ("product", "material", "spec", "material_unit_price", "net_weight", "weight_unit_price", "settlement_total", "weigh_ticket_no", "purpose"):
            vars_[key].set("")
        vars_["qty"].set("1")
        vars_["current_material_gross"].set("--")

    def _edit_batch(self, ctx, _event=None):
        vars_ = ctx.vars_
        selected = ctx.batch_tree.selection()
        if not selected:
            return
        if vars_["product"].get().strip() or vars_["material"].get().strip():
            messagebox.showwarning("先处理当前材料", "请先将正在填写的材料加入本单，或清空当前材料。", parent=ctx.dialog)
            return
        item = ctx.pending_items.pop(int(selected[0]))
        ctx.current_item_id = item.get("item_id")
        for key, source in (("material", "material_name_snapshot"), ("spec", "specification_snapshot"), ("unit", "unit_snapshot"), ("qty", "quantity"), ("purpose", "purpose"), ("net_weight", "net_weight"), ("weight_unit_price", "weight_unit_price"), ("weigh_ticket_no", "weigh_ticket_no")):
            vars_[key].set(str(item.get(source) or ""))
        vars_["product"].set(next((label for label, product in ctx.products_by_label.items() if product["id"] == item.get("product_id")), item["material_name_snapshot"]))
        vars_["settlement_mode"].set(procurement_service.SETTLEMENT_MODES[item.get("settlement_mode", "quantity")])
        vars_["price_basis"].set("含税价" if item.get("price_basis") == "inclusive" else "未税价")
        price_key = "tax_inclusive_unit_price_cents" if item.get("price_basis") == "inclusive" else "material_unit_price_cents"
        vars_["material_unit_price"].set(str(item.get(price_key, 0) / 100))
        vars_["tax_rate"].set(str(item.get("tax_rate_bps", 0) / 100))
        vars_["weight_unit"].set(item.get("weight_unit") or "吨")
        vars_["settlement_total"].set(str((item.get("settlement_total_cents") or 0) / 100))
        self._refresh_batch(ctx)

    def _remove_batch(self, ctx):
        selected = ctx.batch_tree.selection()
        if selected:
            ctx.pending_items.pop(int(selected[0]))
            self._refresh_batch(ctx)

    def _build_batch_panel(self, ctx):
        batch_frame = ttk.Frame(ctx.footer)
        batch_frame.pack(fill=X, pady=4)
        ctx.batch_tree = ttk.Treeview(batch_frame, columns=("name", "quantity", "amount"), show="headings", height=3)
        for key, label in (("name", "本单已加入材料（双击修改）"), ("quantity", "数量 / 重量"), ("amount", "含税金额")):
            ctx.batch_tree.heading(key, text=label)
        ctx.batch_tree.pack(side=LEFT, fill=X, expand=True)
        batch_buttons = ttk.Frame(batch_frame)
        batch_buttons.pack(side=RIGHT, padx=8)
        ctx.attachment_after_save = ttk.BooleanVar(value=False)
        ttk.Checkbutton(batch_buttons, text="保存后上传整单附件", variable=ctx.attachment_after_save).pack(anchor=W)
        ctx.batch_tree.bind("<Double-1>", lambda event: self._edit_batch(ctx, event))
        ttk.Button(batch_buttons, text="加入本单 / 继续填材料", command=lambda: self._save_purchase(ctx, stage_only=True)).pack(fill=X)
        ttk.Button(batch_buttons, text="修改选中材料", command=lambda: self._edit_batch(ctx)).pack(fill=X)
        ttk.Button(batch_buttons, text="删除选中材料", command=lambda: self._remove_batch(ctx)).pack(fill=X)
        ttk.Button(batch_buttons, text="清空当前材料", command=lambda: self._clear_material(ctx)).pack(fill=X)
        self._refresh_batch(ctx)

    def _save_purchase(self, ctx, close_after=True, stage_only=False):
        try:
            self._save_purchase_impl(ctx, close_after=close_after, stage_only=stage_only)
        except Exception:
            show_unexpected_error("无法保存", parent=self.parent)

    def _save_purchase_impl(self, ctx, close_after=True, stage_only=False):
        vars_ = ctx.vars_
        has_current = bool(vars_["material"].get().strip() or vars_["product"].get().strip())
        if not has_current and (stage_only or not ctx.pending_items):
            messagebox.showwarning("请填写材料", "采购单至少需要一条材料明细。", parent=ctx.dialog)
            return
        try:
            datetime.strptime(vars_["date"].get().strip(), "%Y-%m-%d")
            pricing = self._pricing_from_vars(vars_) if has_current else ctx.pending_items[0]
            procurement_service.calculate_purchase_item_amounts(
                {"freight_amount_cents": procurement_service.decimal_minor(vars_["freight"].get() or 0)}, pricing)
        except ValueError as error:
            messagebox.showwarning(
                "提示",
                str(error),
                parent=ctx.dialog,
            )
            return
        supplier_id, product_id, merchant, material = resolve_supplier_product(
            ctx.is_petty,
            merchant_entry=vars_["merchant"].get(),
            material_entry=vars_["material"].get(),
            supplier_entry=vars_["supplier"].get(),
            supplier_map=ctx.supplier_map,
            product_entry=vars_["product"].get(),
            products_by_label=ctx.products_by_label,
            settlement_mode=pricing["settlement_mode"],
        )
        use_equal_allocation = (
            vars_["category"].get()
            == procurement_service.TOOL_EQUIPMENT_CATEGORY
            and vars_["attribution"].get() == "多项目平均分摊"
        )
        allocation_project_ids = self._selected_allocation_project_ids(ctx)
        error = validate_purchase_submission(
            has_current=has_current,
            is_petty=ctx.is_petty,
            merchant=merchant,
            supplier_id=supplier_id,
            product_id=product_id,
            settlement_mode=pricing["settlement_mode"],
            material=material,
            use_equal_allocation=use_equal_allocation,
            allocation_project_count=len(allocation_project_ids),
            project_selected=vars_["project"].get() in ctx.project_map,
        )
        if error:
            title, message, focus = error
            messagebox.showwarning(title, message, parent=ctx.dialog)
            if focus == "product":
                ctx.product_combo.focus_set()
            elif focus == "project":
                ctx.project_combo.focus_set()
            return
        selected_project_id = ctx.project_map.get(vars_["project"].get())
        header = build_order_header(
            ctx.purchase_type,
            project_id=None if use_equal_allocation else selected_project_id,
            project_ids=allocation_project_ids if use_equal_allocation else [],
            allocation_method=resolve_allocation_method(use_equal_allocation, selected_project_id),
            supplier_id=supplier_id,
            merchant=merchant,
            purchase_date=vars_["date"].get().strip(),
            payment_method=vars_["payment_method"].get(),
            payment_status=vars_["payment_status"].get(),
            invoice_status=vars_["invoice"].get(),
            purchaser=vars_["purchaser"].get().strip(),
            freight_text=vars_["freight"].get(),
            notes=vars_["notes"].get().strip(),
        )
        if (
            not ctx.order_id
            and not stage_only
            and header["allocation_method"] == "unassigned"
            and not messagebox.askyesno(
                "确认暂不归集",
                "这笔采购不会进入任何项目成本，将立即出现在“数据治理中心”。\n"
                "确定仍以待归集状态保存吗？",
                parent=ctx.dialog,
            )
        ):
            return
        item = build_order_item(
            item_id=ctx.current_item_id,
            product_id=product_id,
            material=material,
            spec=vars_["spec"].get().strip(),
            unit=vars_["unit"].get().strip(),
            category=vars_["category"].get(),
            pricing=pricing,
            purpose=vars_["purpose"].get().strip(),
            notes=vars_["notes"].get().strip(),
        )
        if stage_only:
            ctx.pending_items.append(item)
            self._clear_material(ctx)
            self._refresh_batch(ctx)
            return
        items = ctx.pending_items + ([item] if has_current else [])
        try:
            if ctx.order_id:
                procurement_service.update_purchase_order(ctx.order_id, header, items)
                saved_order_id = ctx.order_id
            else:
                saved_order_id = procurement_service.add_purchase_order(header, items)
        except ValueError as error:
            messagebox.showwarning("无法保存", str(error), parent=ctx.dialog)
            return
        self.month_var.set(vars_["date"].get()[:7])
        if ctx.attachment_after_save.get():
            open_attachment_manager(self.parent, "purchase", saved_order_id, "采购单", on_change=self.refresh_all)
        if not close_after and not ctx.order_id and not ctx.is_petty:
            ctx.pending_items.clear()
            self._prepare_next_form(ctx, material)
            self._clear_material(ctx)
            self._refresh_batch(ctx)
            self.refresh_filters()
            self.refresh_all()
            return
        ctx.dialog.destroy()
        self.refresh_filters()
        self.refresh_all()

    def _add_purchase_actions(self, ctx):
        add_form_actions(
            ctx.footer,
            cancel_command=ctx.dialog.destroy,
            primary_text=(
                "保存修改"
                if ctx.order_id
                else "保存采购记录"
                if ctx.is_petty
                else "保存整单并新增下一单"
            ),
            primary_command=(
                (lambda: self._save_purchase(ctx))
                if ctx.order_id or ctx.is_petty
                else lambda: self._save_purchase(ctx, close_after=False)
            ),
            secondary_text=(
                "保存并关闭" if not ctx.order_id and not ctx.is_petty else None
            ),
            secondary_command=(
                (lambda: self._save_purchase(ctx, close_after=True))
                if not ctx.order_id and not ctx.is_petty else None
            ),
        )
