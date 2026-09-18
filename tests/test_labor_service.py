import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4


class LaborServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="labor_service_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        from db.backup import backup_database
        _oss_source_db = source_db
        if _oss_source_db.exists():
            backup_database(_oss_source_db, cls.test_db)
        else:
            # 开源环境无生产库：从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_paths = (_conn_module.DB_PATH, _runner_module.DB_PATH)
            _conn_module.DB_PATH = cls.test_db
            _runner_module.DB_PATH = cls.test_db
            try:
                import database as _database
                _database.init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths

        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db

        from services import labor_service, project_service

        cls.labor_service = labor_service
        cls.project_service = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    # ---------- 测试辅助 ----------

    def _query(self, sql, params=()):
        from db.connection import get_connection

        conn = get_connection()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def _make_worker(self, suffix, rate=300, name_prefix="服务测试工人"):
        return self.labor_service.add_worker(
            {
                "name": f"{name_prefix}-{suffix}",
                "trade": "安装",
                "daily_rate": rate,
                "status": "在职",
            }
        )

    def _make_project(self, suffix, prefix="服务测试项目"):
        return self.project_service.create_project(
            {
                "name": f"{prefix}-{suffix}",
                "project_code": f"LS-{suffix}",
                "status": "进行中",
            }
        )

    def _add_log(
        self,
        worker_id,
        work_date,
        work_days=1,
        daily_rate=None,
        project_id=None,
        site=None,
        allow_unassigned=None,
        **extra,
    ):
        data = {
            "worker_id": worker_id,
            "work_date": work_date,
            "construction_site": site or f"服务测试工地-{uuid4().hex[:8]}",
            "work_type": "安装",
            "work_days": work_days,
        }
        if daily_rate is not None:
            data["daily_rate"] = daily_rate
        if project_id is not None:
            data["project_id"] = project_id
        if allow_unassigned is None:
            # 未给项目也未给工地名时，默认显式“待归集”；给了工地名则走名称解析
            allow_unassigned = project_id is None and site is None
        if allow_unassigned:
            data["allow_unassigned"] = True
        data.update(extra)
        return self.labor_service.add_work_log(data)

    def _backdate_rate_version(self, worker_id, effective_from):
        """把工人的工资版本生效日回填到过去，构造可调薪的历史窗口。"""
        from db.connection import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE worker_rate_versions SET effective_from=? WHERE worker_id=?",
                (effective_from, int(worker_id)),
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- 工人 CRUD ----------

    def test_01_worker_crud_search_and_delete(self):
        suffix = uuid4().hex[:8]
        worker_id = self._make_worker(suffix, rate="300.50")

        worker = self.labor_service.get_worker_by_id(worker_id)
        self.assertEqual(worker["name"], f"服务测试工人-{suffix}")
        self.assertEqual(worker["daily_rate"], 300.5)
        versions = self._query(
            "SELECT * FROM worker_rate_versions WHERE worker_id=?", (worker_id,)
        )
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["rate_minor"], 30050)
        self.assertEqual(versions[0]["source"], "worker_creation")
        self.assertEqual(versions[0]["status"], "active")

        hits = self.labor_service.get_workers(keyword=suffix)
        self.assertEqual([row["id"] for row in hits], [worker_id])
        active_hits = self.labor_service.get_workers(keyword=suffix, active_only=True)
        self.assertEqual([row["id"] for row in active_hits], [worker_id])

        self.labor_service.update_worker(
            worker_id,
            {
                "name": f"服务测试工人改-{suffix}",
                "trade": "电工",
                "phone": "13800000000",
                "status": "离职",
                "notes": f"备注-{suffix}",
            },
        )
        updated = self.labor_service.get_worker_by_id(worker_id)
        self.assertEqual(updated["name"], f"服务测试工人改-{suffix}")
        self.assertEqual(updated["trade"], "电工")
        self.assertEqual(updated["status"], "离职")
        self.assertEqual(
            len(self.labor_service.get_workers(keyword=f"备注-{suffix}")), 1
        )
        self.assertEqual(
            self.labor_service.get_workers(keyword=f"备注-{suffix}", active_only=True),
            [],
        )

        with self.assertRaisesRegex(ValueError, "工人不存在"):
            self.labor_service.update_worker(10**9, {"name": "任意"})
        with self.assertRaisesRegex(ValueError, "默认日工资不能为负数"):
            self.labor_service.add_worker(
                {"name": f"负工资-{suffix}", "daily_rate": -1}
            )

        # 无工天、无调薪记录的工人可以删除（建档工资版本行随之清理）
        self.labor_service.delete_workers([worker_id])
        self.assertIsNone(self.labor_service.get_worker_by_id(worker_id))

        # 有调薪审计记录的工人不能删除（审计链不可断）
        adjusted_worker = self._make_worker(uuid4().hex[:8], rate=300)
        self._backdate_rate_version(
            adjusted_worker, (date.today() - timedelta(days=120)).isoformat()
        )
        self.labor_service.apply_rate_adjustment(
            {
                "worker_id": adjusted_worker,
                "new_daily_rate": 350,
                "effective_from": date.today().isoformat(),
                "scope_mode": "future_only",
                "reason": f"删除保护测试-{suffix}",
            }
        )
        with self.assertRaisesRegex(ValueError, "调薪记录"):
            self.labor_service.delete_workers([adjusted_worker])

        logged_worker = self._make_worker(uuid4().hex[:8])
        self._add_log(logged_worker, "2095-02-01", work_days=1)
        with self.assertRaisesRegex(ValueError, "不能删除"):
            self.labor_service.delete_workers([logged_worker])

        self.assertIsNone(self.labor_service.delete_workers([]))

    # ---------- 工天录入校验与归属 ----------

    def test_02_work_log_validation_and_attribution(self):
        suffix = uuid4().hex[:8]
        worker_id = self._make_worker(suffix)

        with self.assertRaisesRegex(ValueError, "工天日期必须是 YYYY-MM-DD"):
            self.labor_service.get_effective_worker_rate(worker_id, "2026/01/01")

        base = {
            "worker_id": worker_id,
            "work_date": "2095-03-01",
            "construction_site": f"校验工地-{suffix}",
            "allow_unassigned": True,
        }
        with self.assertRaisesRegex(ValueError, "工天必须大于 0"):
            self.labor_service.add_work_log(dict(base, work_days=-1))
        with self.assertRaisesRegex(ValueError, "工天必须大于 0"):
            self.labor_service.add_work_log(dict(base, work_days=0))
        with self.assertRaisesRegex(ValueError, "工天必须是有效数字"):
            self.labor_service.add_work_log(dict(base, work_days="abc"))
        with self.assertRaisesRegex(ValueError, "日工资不能为负数"):
            self.labor_service.add_work_log(dict(base, work_days=1, daily_rate=-5))
        with self.assertRaisesRegex(ValueError, "所选项目不存在"):
            self.labor_service.add_work_log(
                {
                    "worker_id": worker_id,
                    "work_date": "2095-03-01",
                    "construction_site": f"校验工地-{suffix}",
                    "work_days": 1,
                    "project_id": 10**9,
                }
            )
        with self.assertRaisesRegex(ValueError, "无法根据工地名称确定所属项目"):
            self.labor_service.add_work_log(
                {
                    "worker_id": worker_id,
                    "work_date": "2095-03-01",
                    "construction_site": f"不存在工地-{suffix}",
                    "work_days": 1,
                }
            )
        with self.assertRaisesRegex(ValueError, "工人不存在"):
            self.labor_service.add_work_log(
                {
                    "worker_id": 10**9,
                    "work_date": "2095-03-01",
                    "construction_site": f"校验工地-{suffix}",
                    "work_days": 1,
                    "allow_unassigned": True,
                }
            )

        # 显式“待归集”可以保存；未填单价时按有效费率计价
        log_id = self._add_log(worker_id, "2095-03-02", work_days=1)
        row = self.labor_service.get_work_log_by_id(log_id)
        self.assertIsNone(row["project_id"])
        self.assertEqual(row["daily_rate_minor"], 30000)
        self.assertEqual(row["amount_minor"], 30000)

        # 显式项目 + 地点：名称不一致被拒绝，一致可保存
        project_id = self._make_project(suffix, "校验归属项目")
        site_name = f"归属地点-{suffix}"
        self.project_service.create_project_site(project_id, {"site_name": site_name})
        site_id = next(
            row["id"]
            for row in self.labor_service.list_work_log_site_options(project_id)
            if row["name"] == site_name
        )
        with self.assertRaisesRegex(ValueError, "施工地点名称与所选项目地点不一致"):
            self.labor_service.add_work_log(
                {
                    "worker_id": worker_id,
                    "work_date": "2095-03-03",
                    "construction_site": f"别的地点-{suffix}",
                    "work_days": 1,
                    "project_id": project_id,
                    "project_site_id": site_id,
                }
            )
        ok_log_id = self.labor_service.add_work_log(
            {
                "worker_id": worker_id,
                "work_date": "2095-03-03",
                "construction_site": site_name,
                "work_days": 1,
                "project_id": project_id,
                "project_site_id": site_id,
            }
        )
        ok_log = self.labor_service.get_work_log_by_id(ok_log_id)
        self.assertEqual(ok_log["project_id"], project_id)
        self.assertEqual(ok_log["project_site_id"], site_id)

        self.assertEqual(self.labor_service.add_work_logs_batch([]), 0)
        self.assertEqual(self.labor_service.set_work_logs_overtime([], True), 0)

    # ---------- 工地别名与项目建议 ----------

    @unittest.skipUnless((Path(__file__).resolve().parent.parent / "supplier_data.db").exists(), "依赖本地生产库数据，开源环境跳过")
    def test_03_site_alias_resolution_and_suggestion(self):
        suffix = uuid4().hex[:8]
        worker_id = self._make_worker(suffix)

        # 内置别名：澄湖 → 澄湖药业（实时库自带项目与同名地点）
        canonical = self._query("SELECT id FROM projects WHERE name='澄湖药业'")
        self.assertTrue(canonical)
        expected_project_id = canonical[0]["id"]
        suggestion = self.labor_service.suggest_project_for_site("澄湖")
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["id"], expected_project_id)
        self.assertEqual(suggestion["name"], "澄湖药业")
        self.assertTrue(suggestion["project_site_id"])

        log_id = self._add_log(worker_id, "2095-04-01", work_days=1, site="澄湖")
        row = self.labor_service.get_work_log_by_id(log_id)
        self.assertEqual(row["project_id"], expected_project_id)
        self.assertEqual(row["construction_site"], "澄湖药业")  # 归一化为规范名

        self.assertIsNone(self.labor_service.suggest_project_for_site(f"不存在-{suffix}"))
        self.assertIsNone(self.labor_service.suggest_project_for_site(""))

        # 自建项目 + 地点：按地点名精确建议项目
        project_id = self._make_project(suffix, "建议项目")
        site_name = f"建议工地-{suffix}"
        self.project_service.create_project_site(project_id, {"site_name": site_name})
        suggestion = self.labor_service.suggest_project_for_site(site_name)
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["id"], project_id)

        log_id = self._add_log(worker_id, "2095-04-02", work_days=1, site=site_name)
        row = self.labor_service.get_work_log_by_id(log_id)
        self.assertEqual(row["project_id"], project_id)

    # ---------- 工天修改 / 工资锁定 / 删除 ----------

    def test_04_update_lock_and_delete_work_logs(self):
        suffix = uuid4().hex[:8]
        worker_id = self._make_worker(suffix)
        log_id = self._add_log(worker_id, "2095-05-10", work_days=0.5, daily_rate=300)

        self.labor_service.update_work_log(
            log_id,
            {
                "worker_id": worker_id,
                "work_date": "2095-05-10",
                "construction_site": f"更新工地-{suffix}",
                "work_type": "改造",
                "work_days": 1,
                "daily_rate": 350,
                "notes": f"更新备注-{suffix}",
                "allow_unassigned": True,
            },
        )
        row = self.labor_service.get_work_log_by_id(log_id)
        self.assertEqual(row["work_days"], 1)
        self.assertEqual(row["daily_rate_minor"], 35000)
        self.assertEqual(row["amount_minor"], 35000)
        self.assertEqual(row["work_type"], "改造")
        self.assertEqual(row["notes"], f"更新备注-{suffix}")

        # 换一个没有记录的日期，避免先撞上每日工天上限校验
        with self.assertRaisesRegex(ValueError, "工天记录不存在或已作废"):
            self.labor_service.update_work_log(
                10**9,
                {
                    "worker_id": worker_id,
                    "work_date": "2095-05-11",
                    "construction_site": f"更新工地-{suffix}",
                    "work_days": 1,
                    "allow_unassigned": True,
                },
            )

        # 锁定 / 解锁
        self.assertEqual(self.labor_service.set_work_logs_rate_locked([], True), 0)
        self.assertEqual(
            self.labor_service.set_work_logs_rate_locked([log_id], True, "核对无误"), 1
        )
        # 重复锁定返回 0（没有状态变化的行）
        self.assertEqual(
            self.labor_service.set_work_logs_rate_locked([log_id], True, "重复锁定"), 0
        )
        row = self.labor_service.get_work_log_by_id(log_id)
        self.assertEqual(row["rate_locked"], 1)
        self.assertEqual(row["rate_lock_reason"], "核对无误")
        self.assertTrue(row["rate_locked_at"])

        with self.assertRaisesRegex(ValueError, "工资已锁定，请先解除锁定"):
            self.labor_service.update_work_log(
                log_id,
                {
                    "worker_id": worker_id,
                    "work_date": "2095-05-10",
                    "construction_site": f"更新工地-{suffix}",
                    "work_days": 1,
                    "allow_unassigned": True,
                },
            )
        with self.assertRaisesRegex(ValueError, "工资已锁定，请先解除锁定"):
            self.labor_service.delete_work_logs([log_id])

        self.assertEqual(self.labor_service.set_work_logs_rate_locked([log_id], False), 1)
        row = self.labor_service.get_work_log_by_id(log_id)
        self.assertEqual(row["rate_locked"], 0)
        self.assertIsNone(row["rate_lock_reason"])
        self.assertIsNone(row["rate_locked_at"])

        events = self._query(
            "SELECT action FROM labor_rate_lock_events WHERE work_log_id=? ORDER BY id",
            (log_id,),
        )
        self.assertEqual([event["action"] for event in events], ["lock", "unlock"])

        # 删除（软作废）
        self.assertIsNone(self.labor_service.delete_work_logs([]))
        self.assertIsNone(self.labor_service.delete_work_logs([log_id]))
        self.assertIsNone(self.labor_service.get_work_log_by_id(log_id))
        status = self._query("SELECT status FROM work_logs WHERE id=?", (log_id,))
        self.assertEqual(status[0]["status"], "void")

    # ---------- 工天查询与月份 ----------

    def test_05_get_work_logs_filters_and_months(self):
        suffix = uuid4().hex[:8]
        worker_id = self._make_worker(suffix, name_prefix="筛选工人")
        first_id = self._add_log(
            worker_id,
            "2096-03-01",
            work_days=1,
            daily_rate=300,
            site=f"阿尔法工地-{suffix}",
            allow_unassigned=True,
            work_type="管线安装",
            notes=f"第一批-{suffix}",
        )
        second_id = self._add_log(
            worker_id,
            "2096-03-02",
            work_days=1,
            daily_rate=300,
            site=f"贝塔工地-{suffix}",
            allow_unassigned=True,
            work_type="设备吊装",
            notes=f"第二批-{suffix}",
        )

        month_rows = [
            row
            for row in self.labor_service.get_work_logs("2096-03")
            if row["worker_id"] == worker_id
        ]
        self.assertEqual(len(month_rows), 2)
        self.assertEqual(month_rows[0]["worker_name"], f"筛选工人-{suffix}")

        site_hits = self.labor_service.get_work_logs(
            "2096-03", keyword=f"阿尔法工地-{suffix}"
        )
        self.assertEqual([row["id"] for row in site_hits], [first_id])
        note_hits = self.labor_service.get_work_logs("", keyword=f"第二批-{suffix}")
        self.assertEqual([row["id"] for row in note_hits], [second_id])
        name_hits = [
            row
            for row in self.labor_service.get_work_logs("2096-03", keyword=suffix)
            if row["worker_id"] == worker_id
        ]
        self.assertEqual(len(name_hits), 2)

        self.assertIn("2096-03", self.labor_service.get_work_months())

    # ---------- 工资率按日期生效 ----------

    def test_06_effective_rate_resolution_by_date(self):
        suffix = uuid4().hex[:8]
        today = date.today()
        first = self._make_worker(uuid4().hex[:8], rate=300)
        second = self._make_worker(uuid4().hex[:8], rate=200)

        self.assertEqual(
            self.labor_service.get_effective_worker_rates([], today.isoformat()), {}
        )
        rates = self.labor_service.get_effective_worker_rates(
            [first, second], today.isoformat()
        )
        self.assertEqual(rates, {first: 300.0, second: 200.0})
        with self.assertRaisesRegex(ValueError, "工人不存在"):
            self.labor_service.get_effective_worker_rate(10**9, today.isoformat())
        with self.assertRaisesRegex(ValueError, "工天日期必须是 YYYY-MM-DD"):
            self.labor_service.get_effective_worker_rates([first], "2026-13-40")

        # 查询早于任何版本生效日的日期：回退到 workers.daily_rate 默认值
        yesterday = (today - timedelta(days=1)).isoformat()
        self.assertEqual(
            self.labor_service.get_effective_worker_rate(first, yesterday), 300.0
        )
        self.assertEqual(
            self.labor_service.get_effective_worker_rates([first, second], yesterday),
            {first: 300.0, second: 200.0},
        )

        # 版本窗口：调薪生效日前后各按当时费率取数
        worker_id = self._make_worker(suffix, rate=300)
        self._backdate_rate_version(
            worker_id, (today - timedelta(days=120)).isoformat()
        )
        effective = (today - timedelta(days=60)).isoformat()
        self.labor_service.apply_rate_adjustment(
            {
                "worker_id": worker_id,
                "new_daily_rate": 400,
                "effective_from": effective,
                "scope_mode": "future_only",
                "reason": f"费率窗口测试-{suffix}",
            }
        )
        day_before = (today - timedelta(days=61)).isoformat()
        self.assertEqual(
            self.labor_service.get_effective_worker_rate(worker_id, day_before), 300.0
        )
        self.assertEqual(
            self.labor_service.get_effective_worker_rate(worker_id, effective), 400.0
        )
        old_version = self._query(
            "SELECT effective_from, effective_to, status FROM worker_rate_versions "
            "WHERE worker_id=? AND rate_minor=30000",
            (worker_id,),
        )[0]
        self.assertEqual(old_version["effective_to"], day_before)
        self.assertEqual(old_version["status"], "active")

    # ---------- 调薪预览 ----------

    def test_07_preview_rate_adjustment_validation_and_amounts(self):
        suffix = uuid4().hex[:8]
        today = date.today()
        today_iso = today.isoformat()
        worker_id = self._make_worker(suffix, rate=300)

        valid = {
            "worker_id": worker_id,
            "new_daily_rate": 400,
            "effective_from": today_iso,
            "scope_mode": "future_only",
            "reason": f"校验-{suffix}",
        }
        with self.assertRaisesRegex(ValueError, "请选择工人"):
            self.labor_service.preview_rate_adjustment({"new_daily_rate": 400})
        with self.assertRaisesRegex(ValueError, "工人不存在"):
            self.labor_service.preview_rate_adjustment(dict(valid, worker_id=10**9))
        with self.assertRaisesRegex(ValueError, "请填写新日工资"):
            self.labor_service.preview_rate_adjustment(
                {key: value for key, value in valid.items() if key != "new_daily_rate"}
            )
        with self.assertRaisesRegex(ValueError, "新日工资必须是有效数字"):
            self.labor_service.preview_rate_adjustment(dict(valid, new_daily_rate="abc"))
        with self.assertRaisesRegex(ValueError, "新日工资不能为负数"):
            self.labor_service.preview_rate_adjustment(dict(valid, new_daily_rate=-1))
        with self.assertRaisesRegex(ValueError, "生效日期必须是 YYYY-MM-DD"):
            self.labor_service.preview_rate_adjustment(dict(valid, effective_from=""))
        with self.assertRaisesRegex(ValueError, "暂不允许未来生效日期"):
            self.labor_service.preview_rate_adjustment(
                dict(valid, effective_from=(today + timedelta(days=1)).isoformat())
            )
        with self.assertRaisesRegex(ValueError, "调薪影响范围无效"):
            self.labor_service.preview_rate_adjustment(dict(valid, scope_mode="bogus"))
        with self.assertRaisesRegex(ValueError, "截止日期必须是 YYYY-MM-DD"):
            self.labor_service.preview_rate_adjustment(
                dict(valid, scope_mode="custom", range_end="")
            )
        with self.assertRaisesRegex(ValueError, "截止日期不能早于生效日期"):
            self.labor_service.preview_rate_adjustment(
                dict(
                    valid,
                    scope_mode="custom",
                    range_end=(today - timedelta(days=1)).isoformat(),
                )
            )
        with self.assertRaisesRegex(ValueError, "截止日期不能晚于今天"):
            self.labor_service.preview_rate_adjustment(
                dict(
                    valid,
                    scope_mode="custom",
                    range_end=(today + timedelta(days=1)).isoformat(),
                )
            )
        with self.assertRaisesRegex(ValueError, "请填写调薪原因"):
            self.labor_service.preview_rate_adjustment(dict(valid, reason="  "))
        # 新建工人的版本今天生效，不允许再插更早的版本
        with self.assertRaisesRegex(ValueError, "不能再插入更早版本"):
            self.labor_service.preview_rate_adjustment(
                dict(valid, effective_from=(today - timedelta(days=1)).isoformat())
            )

        # —— 预览内容：影响范围 / 金额差 / 锁定与未变记录 ——
        worker2 = self._make_worker(uuid4().hex[:8], rate=300)
        self._backdate_rate_version(worker2, (today - timedelta(days=120)).isoformat())
        project_a = self._make_project(uuid4().hex[:8], "预览项目甲")
        project_b = self._make_project(uuid4().hex[:8], "预览项目乙")
        d_before = (today - timedelta(days=100)).isoformat()
        d_in1 = (today - timedelta(days=50)).isoformat()
        d_in2 = (today - timedelta(days=45)).isoformat()
        d_locked = (today - timedelta(days=40)).isoformat()
        d_same = (today - timedelta(days=35)).isoformat()
        effective = (today - timedelta(days=60)).isoformat()
        range_end = (today - timedelta(days=30)).isoformat()

        self._add_log(worker2, d_before, 1, project_id=project_a)  # 窗口外
        log_in1 = self._add_log(worker2, d_in1, 1, daily_rate=300, project_id=project_a)
        self._add_log(worker2, d_in2, 0.5, daily_rate=300, project_id=project_b)
        locked_log = self._add_log(
            worker2, d_locked, 0.5, daily_rate=300, project_id=project_b
        )
        self.labor_service.set_work_logs_rate_locked([locked_log], True, f"锁定-{suffix}")
        self._add_log(worker2, d_same, 0.5, daily_rate=400, project_id=project_a)  # 已是新费率

        preview = self.labor_service.preview_rate_adjustment(
            {
                "worker_id": worker2,
                "new_daily_rate": 400,
                "effective_from": effective,
                "range_end": range_end,
                "scope_mode": "custom",
                "reason": f"普调-{suffix}",
            }
        )
        self.assertNotIn("_rows", preview)
        self.assertEqual(preview["worker"]["id"], worker2)
        self.assertEqual(preview["current_rate_minor"], 30000)
        self.assertEqual(preview["new_rate_minor"], 40000)
        self.assertEqual(preview["effective_from"], effective)
        self.assertEqual(preview["range_end"], range_end)
        self.assertEqual(preview["scope_mode"], "custom")
        self.assertEqual(preview["scope_label"], "自定义日期和项目范围")
        self.assertEqual(preview["candidate_count"], 4)
        self.assertEqual(preview["affected_count"], 2)
        self.assertEqual(preview["skipped_locked_count"], 1)
        self.assertEqual(preview["unchanged_count"], 1)
        self.assertEqual(preview["total_days"], 1.5)
        self.assertEqual(preview["old_amount_minor"], 45000)
        self.assertEqual(preview["new_amount_minor"], 60000)
        self.assertEqual(preview["delta_minor"], 15000)
        impacts = {item["project_id"]: item for item in preview["project_impacts"]}
        self.assertEqual(impacts[project_a]["record_count"], 1)
        self.assertEqual(impacts[project_a]["delta_minor"], 10000)
        self.assertEqual(impacts[project_b]["record_count"], 1)
        self.assertEqual(impacts[project_b]["work_days"], 0.5)
        self.assertEqual(impacts[project_b]["delta_minor"], 5000)

        # 预览不落库：候选工天金额不变
        self.assertEqual(
            self.labor_service.get_work_log_by_id(log_in1)["amount_minor"], 30000
        )

        # future_only：不看存量工天，project_id 被强制清空
        future = self.labor_service.preview_rate_adjustment(
            {
                "worker_id": worker2,
                "new_daily_rate": 400,
                "effective_from": effective,
                "scope_mode": "future_only",
                "project_id": project_a,
                "reason": f"仅未来-{suffix}",
            }
        )
        self.assertEqual(future["candidate_count"], 0)
        self.assertEqual(future["affected_count"], 0)
        self.assertIsNone(future["range_end"])
        self.assertIsNone(future["project_id"])
        self.assertEqual(future["scope_label"], "只影响以后新录入的工天")

        # through_today：截止日期固定为今天
        through = self.labor_service.preview_rate_adjustment(
            {
                "worker_id": worker2,
                "new_daily_rate": 400,
                "effective_from": effective,
                "scope_mode": "through_today",
                "reason": f"到今天-{suffix}",
            }
        )
        self.assertEqual(through["range_end"], today_iso)
        self.assertEqual(through["candidate_count"], 4)
        self.assertEqual(through["scope_label"], "同步调整生效日至今天的已有工天")

        # custom + 项目过滤：只统计所选项目的工天
        scoped = self.labor_service.preview_rate_adjustment(
            {
                "worker_id": worker2,
                "new_daily_rate": 400,
                "effective_from": effective,
                "range_end": range_end,
                "scope_mode": "custom",
                "project_id": project_a,
                "reason": f"按项目-{suffix}",
            }
        )
        self.assertEqual(scoped["project_id"], project_a)
        self.assertEqual(scoped["candidate_count"], 2)
        self.assertEqual(scoped["affected_count"], 1)
        self.assertEqual(scoped["unchanged_count"], 1)
        self.assertEqual(scoped["delta_minor"], 10000)

    # ---------- 调薪应用与审计 ----------

    def test_08_apply_rate_adjustment_full_flow(self):
        suffix = uuid4().hex[:8]
        today = date.today()
        today_iso = today.isoformat()
        worker_id = self._make_worker(suffix, rate=300)
        self._backdate_rate_version(
            worker_id, (today - timedelta(days=120)).isoformat()
        )
        project_a = self._make_project(uuid4().hex[:8], "调薪项目甲")
        project_b = self._make_project(uuid4().hex[:8], "调薪项目乙")
        effective = (today - timedelta(days=60)).isoformat()
        log_before = self._add_log(
            worker_id, (today - timedelta(days=100)).isoformat(), 1, project_id=project_a
        )
        log_in1 = self._add_log(
            worker_id, (today - timedelta(days=50)).isoformat(), 1,
            daily_rate=300, project_id=project_a,
        )
        log_in2 = self._add_log(
            worker_id, (today - timedelta(days=45)).isoformat(), 0.5,
            daily_rate=300, project_id=project_b,
        )
        locked_log = self._add_log(
            worker_id, (today - timedelta(days=40)).isoformat(), 0.5,
            daily_rate=300, project_id=project_b,
        )
        self.labor_service.set_work_logs_rate_locked([locked_log], True, f"已核对-{suffix}")

        result = self.labor_service.apply_rate_adjustment(
            {
                "worker_id": worker_id,
                "new_daily_rate": 400,
                "effective_from": effective,
                "scope_mode": "through_today",
                "reason": f"年度调薪-{suffix}",
            }
        )
        self.assertTrue(result["adjustment_id"])
        adjustment_id = result["adjustment_id"]
        self.assertEqual(result["affected_count"], 2)
        self.assertEqual(result["skipped_locked_count"], 1)
        self.assertEqual(result["unchanged_count"], 0)
        self.assertEqual(result["total_days"], 1.5)
        self.assertEqual(result["old_amount_minor"], 45000)
        self.assertEqual(result["new_amount_minor"], 60000)
        self.assertEqual(result["delta_minor"], 15000)

        # 快照口径：生效日前的不回写、窗口内的重算、锁定的不动
        self.assertEqual(
            self.labor_service.get_work_log_by_id(log_before)["amount_minor"], 30000
        )
        updated1 = self.labor_service.get_work_log_by_id(log_in1)
        self.assertEqual(updated1["daily_rate_minor"], 40000)
        self.assertEqual(updated1["amount_minor"], 40000)
        self.assertEqual(updated1["daily_rate"], 400.0)
        updated2 = self.labor_service.get_work_log_by_id(log_in2)
        self.assertEqual(updated2["daily_rate_minor"], 40000)
        self.assertEqual(updated2["amount_minor"], 20000)
        still_locked = self.labor_service.get_work_log_by_id(locked_log)
        self.assertEqual(still_locked["amount_minor"], 15000)
        self.assertEqual(still_locked["rate_locked"], 1)

        # 工人默认日工资更新，旧版本按生效日前一天关闭，新版本生效
        self.assertEqual(
            self.labor_service.get_worker_by_id(worker_id)["daily_rate"], 400.0
        )
        versions = self._query(
            "SELECT rate_minor, effective_from, effective_to, status, source, reason "
            "FROM worker_rate_versions WHERE worker_id=? ORDER BY id",
            (worker_id,),
        )
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["rate_minor"], 30000)
        self.assertEqual(versions[0]["status"], "active")
        self.assertEqual(
            versions[0]["effective_to"], (today - timedelta(days=61)).isoformat()
        )
        self.assertEqual(versions[1]["rate_minor"], 40000)
        self.assertEqual(versions[1]["effective_from"], effective)
        self.assertEqual(versions[1]["status"], "active")
        self.assertEqual(versions[1]["source"], "adjustment")
        self.assertEqual(versions[1]["reason"], f"年度调薪-{suffix}")

        # 生效日前后按日期取数
        self.assertEqual(
            self.labor_service.get_effective_worker_rate(
                worker_id, (today - timedelta(days=61)).isoformat()
            ),
            300.0,
        )
        self.assertEqual(
            self.labor_service.get_effective_worker_rate(
                worker_id, (today - timedelta(days=59)).isoformat()
            ),
            400.0,
        )

        # 调薪后新录入的工天按新费率计价
        new_log_id = self._add_log(
            worker_id, (today - timedelta(days=20)).isoformat(), 1, project_id=project_a
        )
        new_log = self.labor_service.get_work_log_by_id(new_log_id)
        self.assertEqual(new_log["daily_rate_minor"], 40000)
        self.assertEqual(new_log["amount_minor"], 40000)

        # 审计链：汇总行 + 明细行
        audits = self.labor_service.list_rate_adjustments(worker_id)
        self.assertEqual(len(audits), 1)
        audit = audits[0]
        self.assertEqual(audit["id"], adjustment_id)
        self.assertEqual(audit["old_rate_minor"], 30000)
        self.assertEqual(audit["new_rate_minor"], 40000)
        self.assertEqual(audit["effective_from"], effective)
        self.assertEqual(audit["range_end"], today_iso)
        self.assertEqual(audit["scope_mode"], "through_today")
        self.assertIsNone(audit["project_id"])
        self.assertIsNone(audit["project_name"])
        self.assertEqual(audit["reason"], f"年度调薪-{suffix}")
        self.assertEqual(audit["affected_count"], 2)
        self.assertEqual(audit["skipped_locked_count"], 1)
        self.assertEqual(audit["total_days"], 1.5)
        self.assertEqual(audit["old_amount_minor"], 45000)
        self.assertEqual(audit["new_amount_minor"], 60000)
        self.assertEqual(audit["delta_minor"], 15000)
        self.assertEqual(audit["status"], "applied")

        items = self._query(
            "SELECT work_log_id, old_daily_rate_minor, new_daily_rate_minor, "
            "old_amount_minor, new_amount_minor FROM labor_rate_adjustment_items "
            "WHERE adjustment_id=? ORDER BY work_log_id",
            (adjustment_id,),
        )
        self.assertEqual(
            [item["work_log_id"] for item in items], sorted([log_in1, log_in2])
        )
        for item in items:
            self.assertEqual(item["old_daily_rate_minor"], 30000)
            self.assertEqual(item["new_daily_rate_minor"], 40000)
        amounts = {
            item["work_log_id"]: (item["old_amount_minor"], item["new_amount_minor"])
            for item in items
        }
        self.assertEqual(amounts[log_in1], (30000, 40000))
        self.assertEqual(amounts[log_in2], (15000, 20000))

        # 第二次调薪：审计列表按新→旧排列，limit 生效
        self.labor_service.apply_rate_adjustment(
            {
                "worker_id": worker_id,
                "new_daily_rate": 450,
                "effective_from": today_iso,
                "scope_mode": "future_only",
                "reason": f"二次调薪-{suffix}",
            }
        )
        audits = self.labor_service.list_rate_adjustments(worker_id)
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0]["new_rate_minor"], 45000)
        self.assertEqual(audits[1]["new_rate_minor"], 40000)
        limited = self.labor_service.list_rate_adjustments(worker_id, limit=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0]["new_rate_minor"], 45000)

    def test_09_apply_rate_adjustment_same_day_supersedes_old_version(self):
        suffix = uuid4().hex[:8]
        today = date.today()
        today_iso = today.isoformat()
        worker_id = self._make_worker(suffix, rate=300)
        past_log = self._add_log(
            worker_id, (today - timedelta(days=30)).isoformat(), 1, daily_rate=300
        )

        result = self.labor_service.apply_rate_adjustment(
            {
                "worker_id": worker_id,
                "new_daily_rate": 500,
                "effective_from": today_iso,
                "scope_mode": "future_only",
                "reason": f"入职当天调薪-{suffix}",
            }
        )
        self.assertEqual(result["affected_count"], 0)
        self.assertEqual(result["delta_minor"], 0)
        self.assertIsNone(result["range_end"])

        # 生效日与旧版本相同：旧版本整体作废（superseded）
        versions = self._query(
            "SELECT rate_minor, status, effective_from, effective_to, source "
            "FROM worker_rate_versions WHERE worker_id=? ORDER BY id",
            (worker_id,),
        )
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["source"], "worker_creation")
        self.assertEqual(versions[0]["status"], "superseded")
        self.assertEqual(versions[0]["effective_to"], today_iso)
        self.assertEqual(versions[1]["status"], "active")
        self.assertEqual(versions[1]["rate_minor"], 50000)
        self.assertEqual(versions[1]["effective_from"], today_iso)

        # future_only 不回写存量快照，新录入按新费率
        self.assertEqual(
            self.labor_service.get_work_log_by_id(past_log)["amount_minor"], 30000
        )
        new_log_id = self._add_log(worker_id, today_iso, 1)
        self.assertEqual(
            self.labor_service.get_work_log_by_id(new_log_id)["daily_rate_minor"], 50000
        )
        self.assertEqual(
            self.labor_service.get_effective_worker_rate(worker_id, today_iso), 500.0
        )

        audit = self.labor_service.list_rate_adjustments(worker_id)[0]
        self.assertEqual(audit["scope_mode"], "future_only")
        self.assertIsNone(audit["range_end"])
        self.assertEqual(audit["affected_count"], 0)
        self.assertEqual(audit["old_rate_minor"], 30000)
        self.assertEqual(audit["new_rate_minor"], 50000)

    def test_10_apply_rate_adjustment_custom_project_scope(self):
        suffix = uuid4().hex[:8]
        today = date.today()
        worker_id = self._make_worker(suffix, rate=300)
        self._backdate_rate_version(
            worker_id, (today - timedelta(days=120)).isoformat()
        )
        project_a = self._make_project(uuid4().hex[:8], "范围项目甲")
        project_b = self._make_project(uuid4().hex[:8], "范围项目乙")
        log_a = self._add_log(
            worker_id, (today - timedelta(days=50)).isoformat(), 1,
            daily_rate=300, project_id=project_a,
        )
        log_b = self._add_log(
            worker_id, (today - timedelta(days=49)).isoformat(), 1,
            daily_rate=300, project_id=project_b,
        )
        log_free = self._add_log(
            worker_id, (today - timedelta(days=48)).isoformat(), 1, daily_rate=300
        )

        result = self.labor_service.apply_rate_adjustment(
            {
                "worker_id": worker_id,
                "new_daily_rate": 450,
                "effective_from": (today - timedelta(days=60)).isoformat(),
                "range_end": (today - timedelta(days=30)).isoformat(),
                "scope_mode": "custom",
                "project_id": project_a,
                "reason": f"项目单独调薪-{suffix}",
            }
        )
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["affected_count"], 1)
        self.assertEqual(result["delta_minor"], 15000)
        self.assertEqual(result["project_id"], project_a)
        self.assertEqual(len(result["project_impacts"]), 1)
        self.assertEqual(result["project_impacts"][0]["project_id"], project_a)

        # 只有所选项目的工天被重算
        self.assertEqual(
            self.labor_service.get_work_log_by_id(log_a)["amount_minor"], 45000
        )
        self.assertEqual(
            self.labor_service.get_work_log_by_id(log_b)["amount_minor"], 30000
        )
        self.assertEqual(
            self.labor_service.get_work_log_by_id(log_free)["amount_minor"], 30000
        )

        audit = self.labor_service.list_rate_adjustments(worker_id)[0]
        self.assertEqual(audit["scope_mode"], "custom")
        self.assertEqual(audit["project_id"], project_a)
        project_name = self._query(
            "SELECT name FROM projects WHERE id=?", (project_a,)
        )[0]["name"]
        self.assertEqual(audit["project_name"], project_name)

    # ---------- 汇总口径 ----------

    def test_11_labor_cost_summary_breakdowns(self):
        suffix = uuid4().hex[:8]
        project_id = self._make_project(suffix, "汇总项目")
        first = self._make_worker(uuid4().hex[:8], rate=300, name_prefix="汇总工人甲")
        second = self._make_worker(uuid4().hex[:8], rate=200, name_prefix="汇总工人乙")

        self._add_log(first, "2098-06-01", 1, daily_rate=300, project_id=project_id)
        self._add_log(second, "2098-06-02", 1, daily_rate=200, project_id=project_id)
        self._add_log(first, "2098-07-01", 0.5, daily_rate=300, project_id=project_id)
        self._add_log(second, "2098-06-03", 0.5, daily_rate=200)  # 待归集

        summary = self.labor_service.get_labor_cost_summary(
            start_date="2098-06-01", end_date="2098-07-31", project_id=project_id
        )
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["worker_count"], 2)
        self.assertEqual(summary["work_days"], 2.5)
        self.assertEqual(summary["amount_minor"], 65000)

        self.assertEqual(
            [row["month"] for row in summary["by_month"]], ["2098-06", "2098-07"]
        )
        by_month = {row["month"]: row for row in summary["by_month"]}
        self.assertEqual(by_month["2098-06"]["amount_minor"], 50000)
        self.assertEqual(by_month["2098-06"]["work_days"], 2)
        self.assertEqual(by_month["2098-06"]["record_count"], 2)
        self.assertEqual(by_month["2098-07"]["amount_minor"], 15000)
        self.assertEqual(by_month["2098-07"]["work_days"], 0.5)
        self.assertEqual(by_month["2098-07"]["record_count"], 1)

        by_worker = summary["by_worker"]
        self.assertEqual([row["worker_id"] for row in by_worker], [first, second])
        self.assertEqual(by_worker[0]["amount_minor"], 45000)
        self.assertEqual(by_worker[0]["work_days"], 1.5)
        self.assertEqual(by_worker[0]["record_count"], 2)
        self.assertEqual(by_worker[1]["amount_minor"], 20000)
        self.assertEqual(by_worker[1]["work_days"], 1)

        self.assertEqual(len(summary["details"]), 3)
        self.assertEqual(
            {row["project_name"] for row in summary["details"]}, {f"汇总项目-{suffix}"}
        )

        # 只框 6 月
        june = self.labor_service.get_labor_cost_summary(
            start_date="2098-06-01", end_date="2098-06-30", project_id=project_id
        )
        self.assertEqual(june["record_count"], 2)
        self.assertEqual(june["amount_minor"], 50000)

        # 不按项目过滤：待归集记录进入明细（实时库没有 2098-06 的数据）
        unfiltered = self.labor_service.get_labor_cost_summary(
            start_date="2098-06-01", end_date="2098-06-30"
        )
        self.assertEqual(unfiltered["record_count"], 3)
        self.assertEqual(unfiltered["amount_minor"], 60000)
        names = {row["project_name"] for row in unfiltered["details"]}
        self.assertIn("待归集", names)

    def test_12_work_dashboard_aggregation(self):
        suffix = uuid4().hex[:8]
        project_id = self._make_project(suffix, "看板项目")
        first = self._make_worker(uuid4().hex[:8], rate=300, name_prefix="看板工人甲")
        second = self._make_worker(uuid4().hex[:8], rate=200, name_prefix="看板工人乙")
        site_one = f"看板一号地-{suffix}"
        site_two = f"看板二号地-{suffix}"

        self._add_log(
            first, "2096-08-01", 1, daily_rate=300, project_id=project_id, site=site_one
        )
        self._add_log(
            second, "2096-08-02", 0.5, daily_rate=200, project_id=project_id, site=site_one
        )
        self._add_log(
            first, "2096-08-03", 0.5, daily_rate=300, project_id=project_id, site=site_two
        )

        dashboard = self.labor_service.get_work_dashboard("2096-08")
        summary = dashboard["summary"]
        self.assertEqual(summary["total_days"], 2)
        self.assertEqual(summary["total_amount"], 550)
        self.assertEqual(summary["worker_count"], 2)
        self.assertEqual(summary["site_count"], 2)
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["overtime_days"], 0)
        self.assertEqual(summary["overtime_record_count"], 0)

        by_worker = dashboard["by_worker"]
        self.assertEqual([row["id"] for row in by_worker], [first, second])
        self.assertEqual(by_worker[0]["work_days"], 1.5)
        self.assertEqual(by_worker[0]["amount"], 450)
        self.assertEqual(by_worker[0]["site_count"], 2)
        self.assertEqual(by_worker[1]["work_days"], 0.5)
        self.assertEqual(by_worker[1]["amount"], 100)
        self.assertEqual(by_worker[1]["site_count"], 1)

        by_site = dashboard["by_site"]
        self.assertEqual(
            [row["construction_site"] for row in by_site], [site_one, site_two]
        )
        self.assertEqual(by_site[0]["work_days"], 1.5)
        self.assertEqual(by_site[0]["amount"], 400)
        self.assertEqual(by_site[0]["worker_count"], 2)
        self.assertEqual(by_site[1]["work_days"], 0.5)
        self.assertEqual(by_site[1]["amount"], 150)
        self.assertEqual(by_site[1]["worker_count"], 1)

        empty = self.labor_service.get_work_dashboard("2096-09")["summary"]
        self.assertEqual(empty["record_count"], 0)
        self.assertEqual(empty["total_amount"], 0)
        self.assertEqual(empty["total_days"], 0)

    # ---------- 其它零散入口 ----------

    def test_13_misc_options_and_lookups(self):
        self.assertEqual(self.labor_service.list_work_log_site_options(None), [])
        self.assertEqual(self.labor_service.list_work_log_site_options(0), [])
        self.assertIsNone(self.labor_service.get_worker_by_id(10**9))
        self.assertIsNone(self.labor_service.get_work_log_by_id(10**9))
        self.assertIsInstance(self.labor_service.get_construction_sites(), list)


if __name__ == "__main__":
    unittest.main()
