import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class GovernanceWorkbenchTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import web_workbench_service as business
        from web_workbench.synthetic import seed
        temp = tempfile.TemporaryDirectory(prefix='governance_workbench_')
        self.addCleanup(temp.cleanup)
        for module in (connection, migration_runner):
            override = patch.object(module, 'DB_PATH', Path(temp.name) / 'synthetic.db')
            override.start()
            self.addCleanup(override.stop)
        init_db()
        seed()
        self.business = business
        self.governance = business.governance
        self.order = business.procurement.add_purchase_order({
            'purchase_type':'零星采购','merchant_name_snapshot':'合成五金店',
            'purchase_date':'2026-10-14','freight_amount_cents':1000,
        }, business.prepare_purchase_items([
                {'material_name_snapshot':'合成螺杆','specification_snapshot':'M24','quantity':'12',
                 'unit_snapshot':'支','unit_price':'5.50','tax_percent':'13','price_basis':'exclusive'},
                {'material_name_snapshot':'合成螺帽','specification_snapshot':'M24','quantity':'3',
                 'unit_snapshot':'只','unit_price':'2','tax_percent':'1','price_basis':'inclusive'},
            ]))

    def facts(self):
        from db.connection import db_read
        from services.web_workbench_operations import database_fingerprint
        with db_read() as conn:
            return database_fingerprint(conn)

    def test_pending_summary_list_and_split_exclusion_use_the_same_facts(self):
        second = self.business.save_record('projects',{'name':'合成第二工程','status':'进行中'})
        self.business.procurement.add_purchase_order({
            'purchase_type':'零星采购','purchase_date':'2026-10-14','merchant_name_snapshot':'合成工具店',
            'allocation_method':'equal','project_ids':[1,second],
        }, self.business.prepare_purchase_items([{'material_name_snapshot':'合成电焊机','quantity':'1','unit_snapshot':'台',
                      'unit_price':'100.01','tax_percent':'0','price_basis':'inclusive',
                      'cost_category':self.business.procurement.TOOL_EQUIPMENT_CATEGORY}]))
        before = self.facts()
        rows = self.governance.list_unassigned_purchases()
        summary = self.governance.get_governance_summary()
        self.assertEqual([row['id'] for row in rows],[self.order])
        self.assertEqual(summary['purchase_record_count'],len(rows))
        self.assertEqual(summary['purchase_amount_minor'],9058)
        self.assertEqual(rows[0]['amount_minor'],9058)
        self.assertEqual(rows[0]['total_amount_cents'],9058)
        self.assertEqual(rows[0]['freight_amount_cents'],1000)
        self.assertEqual(self.facts(),before)

    def test_material_keyword_retains_every_line_and_actual_quantities(self):
        row = self.governance.list_unassigned_purchases('螺帽')[0]
        self.assertEqual(row['supplier_name'],'合成五金店')
        self.assertEqual([item['quantity'] for item in row['items']],[12,3])
        self.assertEqual([item['line_amount_cents'] for item in row['items']],[7458,600])
        self.assertIn('合成螺杆',row['material_details'])
        self.assertIn('合成螺帽',row['material_details'])
        self.assertIn('M24',row['material_details'])

    def test_assignment_review_has_source_details_and_target_without_writing(self):
        from services.web_workbench_operations import prepare
        before = self.facts()
        values,review,_ = prepare('governance_purchase',{'ids':[self.order],
            'project_id':1,'reason':'合成明确归集'})
        self.assertEqual(values['ids'],[self.order])
        self.assertEqual(review['total_minor'],9058)
        self.assertEqual(review['source_kind'],'purchases')
        self.assertIn('厂房钢结构',review['notice'])
        self.assertEqual(len(review['rows'][0]['items']),2)
        self.assertEqual(self.facts(),before)
        self.governance.assign_purchase_orders([self.order],1)
        with self.assertRaisesRegex(ValueError,'已归集'):
            prepare('governance_purchase',{'ids':[self.order],'project_id':1,'reason':'合成重复核对'})

    def test_project_name_is_present_and_scope_does_not_hide_unassigned(self):
        row = next(row for row in self.governance.list_project_completeness() if row['id']==1)
        self.assertEqual(row['name'],row['project_name'])
        result = self.business.dashboards('governance',1)
        self.assertEqual([project['id'] for project in result['projects']],[1])
        self.assertIn(self.order,[purchase['id'] for purchase in result['unassigned_purchases']])

    def test_input_invoice_candidates_identify_materials_and_support_material_search(self):
        self.business.entities.assign_records('purchase_orders',[self.order],1,'合成主体确认')
        row = next(row for row in self.business.inputs.cost_candidates(1,keyword='螺帽') if row['id']==self.order)
        self.assertIn('合成螺杆',row['label'])
        self.assertIn('合成螺帽',row['label'])
        self.assertIn('2026-10-14',row['label'])
        self.assertEqual(row['amount_minor'],9058)
