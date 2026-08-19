import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class BusinessPartnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="business_partners_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        if source_db.exists():
            shutil.copy2(source_db, cls.test_db)
        else:
            # 开源环境无生产库时，从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_conn_path = _conn_module.DB_PATH
            _saved_runner_path = _runner_module.DB_PATH
            _conn_module.DB_PATH = cls.test_db
            _runner_module.DB_PATH = cls.test_db
            try:
                import database as _database
                _database.init_db()  # 建基础表 + run_migrations()
            finally:
                _conn_module.DB_PATH = _saved_conn_path
                _runner_module.DB_PATH = _saved_runner_path

        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db
        from services import master_data_service, project_service
        from services import contract_service, procurement_service

        cls.partners = master_data_service
        cls.projects = project_service
        cls.contracts = contract_service
        cls.procurement = procurement_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _payload(self, suffix, roles):
        return {
            "legal_name": f"客商测试单位-{suffix}",
            "short_name": f"客商-{suffix}",
            "unified_credit_code": f"91330000{suffix.upper()}",
            "roles": roles,
            "status": "active",
            "registered_address": "测试注册地址",
            "business_address": "测试经营地址",
            "invoice_phone": "0576-12345678",
            "bank_name": "测试银行",
            "bank_account": "622200001234",
            "contact_name": "测试联系人",
            "contact_phone": "13800000000",
            "contact_email": "test@example.com",
            "customer_category": "重点客户",
            "settlement_terms": "月结30天",
            "credit_limit": "500000.25",
            "supplier_category": "钢材、瓦料",
            "default_tax_rate_percent": "13",
            "price_level": "中",
            "delivery_rating": "较快",
            "quality_rating": "优",
            "export_capability": "否",
            "notes": "双角色档案测试",
        }

    def test_dual_role_profile_round_trip(self):
        suffix = uuid4().hex[:8]
        partner_id = self.partners.create_business_partner(
            self._payload(suffix, {"customer", "supplier"})
        )
        profile = self.partners.get_business_partner(partner_id)
        self.assertEqual(profile["roles"], {"customer", "supplier"})
        self.assertEqual(profile["credit_limit_minor"], 50000025)
        self.assertEqual(profile["default_tax_rate_bps"], 1300)
        self.assertEqual(profile["contact_phone"], "13800000000")
        self.assertTrue(any(row["id"] == partner_id for row in self.partners.list_customers()))
        self.assertTrue(any(row["id"] == partner_id for row in self.partners.list_suppliers()))

        updated = dict(self._payload(suffix, {"customer", "supplier"}))
        updated["short_name"] = "更新简称"
        updated["contact_phone"] = "13900000000"
        self.partners.update_business_partner(partner_id, updated)
        profile = self.partners.get_business_partner(partner_id)
        self.assertEqual(profile["short_name"], "更新简称")
        self.assertEqual(profile["contact_phone"], "13900000000")

    def test_duplicate_name_and_role_removal_are_guarded(self):
        suffix = uuid4().hex[:8]
        payload = self._payload(suffix, {"customer"})
        partner_id = self.partners.create_business_partner(payload)
        with self.assertRaisesRegex(ValueError, "同名客商"):
            self.partners.create_business_partner(payload)

        project_id = self.projects.create_project(
            {
                "name": f"客商角色保护项目-{suffix}",
                "project_code": f"BP-{suffix}",
                "customer_name": payload["legal_name"],
                "status": "进行中",
            }
        )
        self.assertTrue(project_id)
        changed = dict(payload)
        changed["roles"] = {"other"}
        with self.assertRaisesRegex(ValueError, "已有项目历史"):
            self.partners.update_business_partner(partner_id, changed)

    def test_deactivation_preserves_historical_role(self):
        suffix = uuid4().hex[:8]
        partner_id = self.partners.create_business_partner(
            self._payload(suffix, {"supplier"})
        )
        self.partners.deactivate_business_partners([partner_id])
        profile = self.partners.get_business_partner(partner_id)
        self.assertEqual(profile["status"], "inactive")
        self.assertIn("supplier", profile["roles"])
        self.assertFalse(any(row["id"] == partner_id for row in self.partners.list_suppliers()))

    def test_contract_free_text_creates_pending_customer_partner(self):
        suffix = uuid4().hex[:8]
        customer_name = f"合同待确认客户-{suffix}"
        contract_id = self.contracts.create_contract(
            {
                "name": f"客商合同测试-{suffix}",
                "customer_name": customer_name,
                "contract_type": "project",
                "sign_date": "2099-08-01",
                "amount": "10000",
                "status": "active",
            }
        )
        contract = self.contracts.get_contract(contract_id)
        self.assertTrue(contract["customer_partner_id"])
        profile = self.partners.get_business_partner(contract["customer_partner_id"])
        self.assertEqual(profile["legal_name"], customer_name)
        self.assertEqual(profile["status"], "pending")
        self.assertIn("customer", profile["roles"])
        self.assertEqual(profile["credit_limit_minor"], 0)

    def test_inactive_customer_and_wrong_supplier_role_are_rejected(self):
        suffix = uuid4().hex[:8]
        customer_id = self.partners.create_business_partner(
            self._payload(suffix, {"customer"})
        )
        self.partners.deactivate_business_partners([customer_id])
        with self.assertRaisesRegex(ValueError, "已停用"):
            self.projects.create_project(
                {
                    "name": f"停用客户项目-{suffix}",
                    "project_code": f"IC-{suffix}",
                    "customer_name": f"客商测试单位-{suffix}",
                    "customer_partner_id": customer_id,
                    "status": "进行中",
                }
            )
        with self.assertRaisesRegex(ValueError, "不具备供应商角色"):
            self.procurement.add_purchase_order(
                {
                    "purchase_type": "零星采购",
                    "supplier_id": customer_id,
                    "merchant_name_snapshot": f"客商测试单位-{suffix}",
                    "purchase_date": "2099-08-01",
                },
                {
                    "material_name_snapshot": "测试材料",
                    "quantity": 1,
                    "unit_price_cents": 100,
                    "line_amount_cents": 100,
                },
            )


if __name__ == "__main__":
    unittest.main()
