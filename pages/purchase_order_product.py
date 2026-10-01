"""Supplier-offer typeahead and quote snapshot handling for purchase entry."""

from services import master_data_service
from ui.typeahead import filter_supplier_offer_labels


class PurchaseOrderProductMixin:
    def _clear_product_details(self, ctx):
        for key in (
            "material",
            "spec",
            "unit",
            "material_unit_price",
            "tax_rate",
        ):
            ctx.vars_[key].set("")

    def _load_products(self, ctx, *_args):
        vars_ = ctx.vars_
        products_by_label = ctx.products_by_label

        supplier_id = ctx.supplier_map.get(vars_["supplier"].get())
        products = master_data_service.list_supplier_offers(supplier_id=supplier_id) if supplier_id else []
        products_by_label.clear()
        for product in products:
            label = f"{product['name']} · {product['specification']}"
            if label in products_by_label:
                label = f"{label} · ID {product['id']}"
            products_by_label[label] = product
        ctx.product_combo["values"] = filter_supplier_offer_labels(
            products_by_label, ""
        )
        vars_["product"].set("")
        self._clear_product_details(ctx)
        if products_by_label:
            ctx.product_hint_var.set(
                f"当前供应商有 {len(products_by_label)} 条报价；输入材料名称或规格筛选。"
            )
        elif vars_["settlement_mode"].get() == "按数量":
            ctx.product_hint_var.set("当前供应商没有可用材料报价，请先维护材料与供应商报价。")
        else:
            ctx.product_hint_var.set("可直接填写整批材料名称，无需先建立单件报价。")

    def _filter_products(self, ctx, event=None):
        vars_ = ctx.vars_
        products_by_label = ctx.products_by_label
        if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        query = vars_["product"].get().strip()
        if vars_["settlement_mode"].get() != "按数量":
            vars_["material"].set(query)
            return
        labels = filter_supplier_offer_labels(products_by_label, query)
        ctx.product_combo["values"] = labels
        if query not in products_by_label:
            self._clear_product_details(ctx)
        if query:
            if labels:
                ctx.product_hint_var.set(
                    f"找到 {len(labels)} 条匹配；可按方向键选择并按回车确认。"
                )
                if event:
                    ctx.product_combo.after_idle(
                        lambda: ctx.product_combo.event_generate("<Down>")
                    )
            else:
                ctx.product_hint_var.set(
                    "没有匹配材料，请更换关键词或先到“材料与供应商报价”新增报价。"
                )
        elif products_by_label:
            ctx.product_hint_var.set(
                f"当前供应商有 {len(products_by_label)} 条报价；输入材料名称或规格筛选。"
            )

    def _select_product(self, ctx, *_args):
        vars_ = ctx.vars_
        product = ctx.products_by_label.get(vars_["product"].get())
        if product:
            vars_["material"].set(product["name"])
            vars_["spec"].set(product["specification"] or "")
            if vars_["settlement_mode"].get() == "按数量":
                vars_["unit"].set(product["unit"] or "")
                vars_["price_basis"].set("含税价" if product.get("price_basis") == "inclusive" else "未税价")
                vars_["material_unit_price"].set(str(product.get("quoted_price", product["price"]) or 0))
            vars_["tax_rate"].set(str(product["tax_rate_percent"] or 0))
            ctx.product_hint_var.set("按数量使用目录报价；过磅或总额采购请填写本次结算数据，不套用目录单价。")

    def _setup_product_typeahead(self, ctx):
        vars_ = ctx.vars_
        ctx.supplier_combo.bind("<<ComboboxSelected>>", lambda *args: self._load_products(ctx))
        ctx.product_combo.bind("<<ComboboxSelected>>", lambda *args: self._select_product(ctx))
        ctx.product_combo.bind("<KeyRelease>", lambda event: self._filter_products(ctx, event))
        self._load_products(ctx)
        order_id = ctx.order_id
        edit_data = ctx.edit_data
        if order_id and edit_data.get("product_id"):
            for label, product in ctx.products_by_label.items():
                if product["id"] == edit_data["product_id"]:
                    vars_["product"].set(label)
                    break
            self._select_product(ctx)
            # 历史成交单价以采购快照为准，不能被当前产品目录价格覆盖。
            vars_["price_basis"].set("含税价" if edit_data.get("price_basis") == "inclusive" else "未税价")
            vars_["material_unit_price"].set(
                str(edit_data.get("tax_inclusive_unit_price_cents" if edit_data.get("price_basis") == "inclusive" else "material_unit_price_cents", 0) / 100)
            )
            vars_["tax_rate"].set(
                str(edit_data.get("tax_rate_bps", 0) / 100)
            )
            vars_["qty"].set(str(edit_data.get("quantity", 1)))
            vars_["freight"].set(
                str(edit_data.get("freight_amount_cents", 0) / 100)
            )

        if order_id and not edit_data.get("product_id"):
            vars_["product"].set(edit_data.get("material_name_snapshot", ""))
            vars_["material"].set(edit_data.get("material_name_snapshot", ""))
            vars_["spec"].set(edit_data.get("specification_snapshot", ""))
            vars_["tax_rate"].set(str(edit_data.get("tax_rate_bps", 0) / 100))
