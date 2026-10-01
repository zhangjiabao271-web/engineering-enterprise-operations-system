import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pages.purchase_order_dialog import (
    allocation_preview_text,
    build_order_header,
    build_order_item,
    build_pricing_item,
    format_purchase_amounts,
    initial_form_values,
    queued_line_total_cents,
    resolve_allocation_method,
    resolve_supplier_product,
    validate_purchase_submission,
)
from services import procurement_service


def _money(cents):
    return f"¥{cents / 100:.2f}"


class BuildPricingItemTests(unittest.TestCase):
    def test_quantity_mode_inclusive(self):
        item = build_pricing_item({
            "settlement_mode": "按数量", "qty": "2", "price_basis": "含税价",
            "tax_rate": "13", "material_unit_price": "100",
            "net_weight": "", "weight_unit": "吨", "weight_unit_price": "",
            "settlement_total": "", "weigh_ticket_no": "A-1",
        })
        self.assertEqual(item["settlement_mode"], "quantity")
        self.assertEqual(item["quantity"], "2")
        self.assertEqual(item["price_basis"], "inclusive")
        self.assertEqual(item["tax_rate_bps"], 1300)
        self.assertEqual(item["material_unit_price_cents"], 10000)
        self.assertEqual(item["tax_inclusive_unit_price_cents"], 10000)
        self.assertEqual(item["weigh_ticket_no"], "A-1")
        self.assertNotIn("settlement_total_cents", item)

    def test_quantity_mode_exclusive_label(self):
        item = build_pricing_item({
            "settlement_mode": "按数量", "qty": "1", "price_basis": "未税价",
            "tax_rate": "0", "material_unit_price": "10",
            "net_weight": "", "weight_unit": "吨", "weight_unit_price": "",
            "settlement_total": "", "weigh_ticket_no": "",
        })
        self.assertEqual(item["price_basis"], "exclusive")

    def test_total_mode_uses_settlement_total(self):
        item = build_pricing_item({
            "settlement_mode": "按结算总额", "qty": "1", "price_basis": "含税价",
            "tax_rate": "13", "material_unit_price": "",
            "net_weight": "", "weight_unit": "吨", "weight_unit_price": "",
            "settlement_total": "500", "weigh_ticket_no": "",
        })
        self.assertEqual(item["settlement_mode"], "total")
        self.assertEqual(item["settlement_total_cents"], 50000)
        self.assertNotIn("material_unit_price_cents", item)

    def test_weight_mode_passes_weight_fields(self):
        item = build_pricing_item({
            "settlement_mode": "按过磅重量", "qty": "1", "price_basis": "含税价",
            "tax_rate": "13", "material_unit_price": "",
            "net_weight": "5.26", "weight_unit": "吨", "weight_unit_price": "3800",
            "settlement_total": "", "weigh_ticket_no": "W-9",
        })
        self.assertEqual(item["settlement_mode"], "weight")
        self.assertEqual(item["net_weight"], "5.26")
        self.assertEqual(item["weight_unit_price"], "3800")
        self.assertNotIn("material_unit_price_cents", item)
        self.assertNotIn("settlement_total_cents", item)


class CalculationParityTests(unittest.TestCase):
    """The dialog must delegate amount math to the service layer unchanged."""

    def test_quantity_matches_calculate_purchase_amounts(self):
        item = build_pricing_item({
            "settlement_mode": "按数量", "qty": "2", "price_basis": "含税价",
            "tax_rate": "13", "material_unit_price": "100",
            "net_weight": "", "weight_unit": "吨", "weight_unit_price": "",
            "settlement_total": "", "weigh_ticket_no": "",
        })
        dialog_amounts = procurement_service.calculate_purchase_item_amounts(
            {"freight_amount_cents": 500}, item)
        service_amounts = procurement_service.calculate_purchase_amounts(
            2, 10000, 1300, 500,
            price_basis="inclusive", tax_inclusive_unit_price_cents=10000)
        self.assertEqual(dialog_amounts, service_amounts)

    def test_weight_matches_service_contract(self):
        item = build_pricing_item({
            "settlement_mode": "按过磅重量", "qty": "1", "price_basis": "含税价",
            "tax_rate": "13", "material_unit_price": "",
            "net_weight": "5.26", "weight_unit": "吨", "weight_unit_price": "3800",
            "settlement_total": "", "weigh_ticket_no": "",
        })
        amounts = procurement_service.calculate_purchase_item_amounts(
            {"freight_amount_cents": 10000}, item)
        self.assertEqual(amounts["line_amount_cents"], 1998800)
        self.assertEqual(amounts["project_cost_cents"], 2008800)


class FormatPurchaseAmountsTests(unittest.TestCase):
    def test_formats_all_summary_fields(self):
        amounts = {
            "line_amount_cents": 22600,
            "tax_inclusive_unit_price_cents": 11300,
            "material_amount_cents": 20000,
            "tax_amount_cents": 2600,
            "project_cost_cents": 22650,
        }
        self.assertEqual(format_purchase_amounts(amounts, 7400), {
            "current_material_gross": "¥226.00",
            "tax_inclusive_unit_price": "113.00",
            "material_amount": "200.00",
            "material_gross": "300.00",
            "tax_amount": "26.00",
            "project_cost": "300.50",
        })

    def test_queued_total_defaults_to_zero(self):
        amounts = {
            "line_amount_cents": 100,
            "tax_inclusive_unit_price_cents": 100,
            "material_amount_cents": 100,
            "tax_amount_cents": 0,
            "project_cost_cents": 100,
        }
        formatted = format_purchase_amounts(amounts, 0)
        self.assertEqual(formatted["material_gross"], "1.00")
        self.assertEqual(formatted["project_cost"], "1.00")


class QueuedLineTotalTests(unittest.TestCase):
    def test_sums_staged_items(self):
        items = [
            {"settlement_mode": "total", "settlement_total_cents": 10000,
             "price_basis": "inclusive", "tax_rate_bps": 0},
            {"settlement_mode": "total", "settlement_total_cents": 25000,
             "price_basis": "inclusive", "tax_rate_bps": 0},
        ]
        self.assertEqual(queued_line_total_cents(items), 35000)
        self.assertEqual(queued_line_total_cents([]), 0)


class AllocationPreviewTextTests(unittest.TestCase):
    labels = {1: "PRJ-1 · 项目一", 2: "PRJ-2 · 项目二"}

    def test_single_project_attribution_shows_nothing(self):
        self.assertEqual(
            allocation_preview_text("单项目归集", "300.00", [1, 2], self.labels, _money),
            "",
        )

    def test_needs_at_least_two_projects(self):
        self.assertEqual(
            allocation_preview_text("多项目平均分摊", "300.00", [1], self.labels, _money),
            "请至少勾选两个项目，金额会自动平均分摊。",
        )

    def test_equal_split_with_remainder(self):
        text = allocation_preview_text("多项目平均分摊", "300.01", [1, 2], self.labels, _money)
        self.assertEqual(
            text,
            "分摊预览：\nPRJ-1 · 项目一  ¥150.01\nPRJ-2 · 项目二  ¥150.00",
        )

    def test_invalid_amount_shows_hint(self):
        self.assertEqual(
            allocation_preview_text("多项目平均分摊", "--", [1, 2], self.labels, _money),
            "填写数量和价格后，这里会显示分摊结果。",
        )


class ResolveTests(unittest.TestCase):
    def test_allocation_method(self):
        self.assertEqual(resolve_allocation_method(True, 5), "equal")
        self.assertEqual(resolve_allocation_method(False, 5), "direct")
        self.assertEqual(resolve_allocation_method(False, None), "unassigned")

    def test_petty_uses_merchant_text(self):
        self.assertEqual(
            resolve_supplier_product(
                True,
                merchant_entry="  建材店 ", material_entry=" 钉子 ",
                supplier_entry="", supplier_map={}, product_entry="",
                products_by_label={}, settlement_mode="quantity",
            ),
            (None, None, "建材店", "钉子"),
        )

    def test_formal_quantity_resolves_supplier_and_product(self):
        self.assertEqual(
            resolve_supplier_product(
                False,
                merchant_entry="", material_entry=" H型钢 ",
                supplier_entry="供应商A", supplier_map={"供应商A": 7},
                product_entry="H型钢 · H300", products_by_label={"H型钢 · H300": {"id": 9}},
                settlement_mode="quantity",
            ),
            (7, 9, "供应商A", "H型钢"),
        )

    def test_formal_non_quantity_without_product_falls_back_to_product_text(self):
        self.assertEqual(
            resolve_supplier_product(
                False,
                merchant_entry="", material_entry="被忽略",
                supplier_entry="供应商A", supplier_map={"供应商A": 7},
                product_entry=" 整批钢材 ", products_by_label={},
                settlement_mode="total",
            ),
            (7, None, "供应商A", "整批钢材"),
        )


class ValidatePurchaseSubmissionTests(unittest.TestCase):
    def _valid(self, **overrides):
        kwargs = {
            "has_current": True,
            "is_petty": False,
            "merchant": "供应商A",
            "supplier_id": 7,
            "product_id": 9,
            "settlement_mode": "quantity",
            "material": "H型钢",
            "use_equal_allocation": False,
            "allocation_project_count": 0,
            "project_selected": True,
        }
        kwargs.update(overrides)
        return kwargs

    def test_valid_passes(self):
        self.assertIsNone(validate_purchase_submission(**self._valid()))

    def test_missing_merchant(self):
        error = validate_purchase_submission(**self._valid(merchant=""))
        self.assertEqual(error, ("提示", "请选择供应商或填写商户名称", None))

    def test_formal_requires_supplier_id(self):
        error = validate_purchase_submission(**self._valid(supplier_id=None))
        self.assertEqual(error[1], "请选择供应商或填写商户名称")

    def test_quantity_mode_requires_picked_product(self):
        error = validate_purchase_submission(**self._valid(product_id=None))
        self.assertEqual(error[2], "product")

    def test_non_quantity_mode_allows_free_text_material(self):
        self.assertIsNone(validate_purchase_submission(
            **self._valid(product_id=None, settlement_mode="total", material="整批钢材")))

    def test_current_material_requires_name(self):
        error = validate_purchase_submission(**self._valid(material=""))
        self.assertEqual(error[1], "请完整填写供应商/商户和材料信息")

    def test_equal_allocation_needs_two_projects(self):
        error = validate_purchase_submission(
            **self._valid(use_equal_allocation=True, allocation_project_count=1))
        self.assertEqual(error[1], "工具和设备多项目平均分摊至少需要勾选两个项目。")

    def test_project_must_be_picked_from_list(self):
        error = validate_purchase_submission(**self._valid(project_selected=False))
        self.assertEqual(error[0], "请选择项目")
        self.assertEqual(error[2], "project")

    def test_first_error_wins(self):
        error = validate_purchase_submission(
            **self._valid(merchant="", project_selected=False))
        self.assertEqual(error[1], "请选择供应商或填写商户名称")


class BuildPayloadTests(unittest.TestCase):
    def test_header_parses_freight(self):
        header = build_order_header(
            "正式采购",
            project_id=3, project_ids=[], allocation_method="direct",
            supplier_id=7, merchant="供应商A", purchase_date="2026-09-20",
            payment_method="对公转账", payment_status="未确认", invoice_status="未确认",
            purchaser="张三", freight_text="12.34", notes="备注",
        )
        self.assertEqual(header["freight_amount_cents"], 1234)
        self.assertEqual(header["project_id"], 3)
        self.assertEqual(header["merchant_name_snapshot"], "供应商A")
        self.assertEqual(header["purchase_date"], "2026-09-20")

    def test_header_blank_freight_is_zero(self):
        header = build_order_header(
            "零星采购",
            project_id=None, project_ids=[1, 2], allocation_method="equal",
            supplier_id=None, merchant="建材店", purchase_date="2026-09-20",
            payment_method="微信", payment_status="已付款", invoice_status="无发票",
            purchaser="", freight_text="", notes="",
        )
        self.assertEqual(header["freight_amount_cents"], 0)
        self.assertEqual(header["project_ids"], [1, 2])

    def test_item_merges_pricing(self):
        pricing = {"settlement_mode": "quantity", "quantity": "2",
                   "material_unit_price_cents": 10000}
        item = build_order_item(
            item_id=11, product_id=9, material="H型钢", spec="H300", unit="吨",
            category="材料费", pricing=pricing, purpose="主体结构", notes="",
        )
        self.assertEqual(item["item_id"], 11)
        self.assertEqual(item["material_name_snapshot"], "H型钢")
        self.assertEqual(item["material_unit_price_cents"], 10000)
        self.assertEqual(item["quantity"], "2")
        self.assertEqual(item["purpose"], "主体结构")


class InitialFormValuesTests(unittest.TestCase):
    def test_new_petty_defaults(self):
        values = initial_form_values(True, False, {}, "待归集（稍后分配）", "")
        self.assertEqual(values["payment_method"], "微信")
        self.assertEqual(values["payment_status"], "已付款")
        self.assertEqual(values["invoice"], "无发票")
        self.assertEqual(values["material_unit_price"], "")
        self.assertEqual(values["tax_rate"], "0")
        self.assertEqual(values["unit"], "件")
        self.assertEqual(values["attribution"], "单项目归集")

    def test_new_formal_defaults(self):
        values = initial_form_values(False, False, {}, "待归集（稍后分配）", "")
        self.assertEqual(values["payment_method"], "对公转账")
        self.assertEqual(values["payment_status"], "未确认")
        self.assertEqual(values["invoice"], "未确认")
        self.assertEqual(values["settlement_mode"], "按数量")

    def test_edit_formats_minor_units(self):
        edit_data = {
            "settlement_mode": "total",
            "settlement_total_cents": 12345,
            "price_basis": "exclusive",
            "material_unit_price_cents": 999,
            "tax_rate_bps": 1300,
            "tax_inclusive_unit_price_cents": 1129,
            "material_amount_cents": 9990,
            "tax_amount_cents": 1299,
            "freight_amount_cents": 500,
            "project_cost_cents": 11788,
            "allocation_method": "equal",
        }
        values = initial_form_values(False, True, edit_data, "P · 项目", "供应商A")
        self.assertEqual(values["settlement_mode"], "按结算总额")
        self.assertEqual(values["settlement_total"], "123.45")
        self.assertEqual(values["price_basis"], "未税价")
        self.assertEqual(values["material_unit_price"], "9.99")
        self.assertEqual(values["tax_rate"], "13")
        self.assertEqual(values["freight"], "5.00")
        self.assertEqual(values["project_cost"], "117.88")
        self.assertEqual(values["attribution"], "多项目平均分摊")

    def test_edit_inclusive_uses_gross_price(self):
        edit_data = {"price_basis": "inclusive", "tax_inclusive_unit_price_cents": 11300}
        values = initial_form_values(False, True, edit_data, "P", "S")
        self.assertEqual(values["material_unit_price"], "113.0")
        self.assertEqual(values["price_basis"], "含税价")


class PurchaseSaveCallbackTests(unittest.TestCase):
    def setUp(self):
        from pages.purchase_order_dialog import PurchaseOrderDialogMixin

        self.page = PurchaseOrderDialogMixin()
        self.page.parent = Mock()
        self.ctx = SimpleNamespace(dialog=Mock())

    def test_business_error_is_shown_without_closing(self):
        self.ctx.vars_ = {
            key: SimpleNamespace(get=lambda value=value: value)
            for key, value in {
                "material": "钢板", "product": "", "date": "2026-09-28",
            }.items()
        }
        self.ctx.pending_items = []
        with patch.object(self.page, "_pricing_from_vars", side_effect=ValueError("金额无效")), \
             patch("pages.purchase_order_dialog.messagebox.showwarning") as warning, \
             patch("pages.purchase_order_dialog.show_unexpected_error") as unexpected:
            self.page._save_purchase(self.ctx)
        warning.assert_called_once_with("提示", "金额无效", parent=self.ctx.dialog)
        unexpected.assert_not_called()
        self.ctx.dialog.destroy.assert_not_called()

    def test_unexpected_error_is_safe_and_preserves_form(self):
        with patch.object(self.page, "_save_purchase_impl", side_effect=RuntimeError("private database detail")), \
             patch("pages.purchase_order_dialog.messagebox.showwarning") as warning, \
             patch("pages.purchase_order_dialog.show_unexpected_error") as unexpected:
            self.page._save_purchase(self.ctx)
        unexpected.assert_called_once_with("无法保存", parent=self.page.parent)
        warning.assert_not_called()
        self.ctx.dialog.destroy.assert_not_called()

    def test_save_options_reach_implementation(self):
        with patch.object(self.page, "_save_purchase_impl") as save:
            self.page._save_purchase(self.ctx, close_after=False, stage_only=True)
        save.assert_called_once_with(self.ctx, close_after=False, stage_only=True)


if __name__ == "__main__":
    unittest.main()
