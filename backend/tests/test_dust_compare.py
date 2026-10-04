"""粉尘治理前后对比口径的回归测试。

只依赖标准库：运行方式
    cd backend && python3 -m unittest discover -s tests -v
（服务跑在内存仓库上，每个用例前重置 store，互不影响。）
"""
from __future__ import annotations

import unittest
from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.services import dust_compare
from app.store import store


def segment(view: dict, point_id: int, index: int) -> dict:
    for group in view["groups"]:
        for row in group["rows"]:
            if row["point_id"] == point_id and row["段序号"] == index:
                return row
    raise AssertionError(f"测点 {point_id} 第 {index} 段不存在")


class DustCompareTest(unittest.TestCase):
    def setUp(self) -> None:
        # store 是内存单例，从种子重新拷贝一份，保证用例间隔离。
        from app.seed import SEED_ROWS

        store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        self.as_of = date(2026, 9, 30)

    def view(self, **kwargs):
        return dust_compare.build_comparison(as_of=self.as_of, **kwargs)

    def test_basic_efficiency_and_descending_order(self):
        view = self.view()
        # 1004：11.2 -> 3.6 = 67.857... -> 67.9
        row = segment(view, 4, 1)
        self.assertEqual(row["前读数"]["value"], 11.2)
        self.assertEqual(row["后读数"]["value"], 3.6)
        self.assertEqual(row["降尘效率"], 67.9)
        # 区域内按效率降序；回风大巷第一条应是效率最高的 1004
        first = view["groups"][0]["rows"][0]
        self.assertEqual(first["point_id"], 4)
        for group in view["groups"]:
            effs = [r["降尘效率"] for r in group["rows"]]
            valid = [e for e in effs if e is not None]
            self.assertEqual(valid, sorted(valid, reverse=True))
            # 留空段一律排在该区域末尾
            self.assertTrue(all(e is not None for e in effs[:len(valid)]))

    def test_measure_change_segments_by_effective_date(self):
        # 1001 措施从喷雾降尘（08-03）变更为煤层注水（09-06），必须切成两段
        view = self.view()
        seg1, seg2 = segment(view, 1, 1), segment(view, 1, 2)
        self.assertEqual(seg1["降尘措施"], "喷雾降尘")
        self.assertEqual(seg1["区间起"], "2026-08-03")
        self.assertEqual(seg1["区间止"], "2026-09-06")
        self.assertEqual(seg1["降尘效率"], 48.0)  # 10.0 -> 5.2
        self.assertEqual(seg2["降尘措施"], "煤层注水")
        self.assertEqual(seg2["区间起"], "2026-09-06")
        self.assertIsNone(seg2["区间止"])
        self.assertEqual(seg2["前读数"]["value"], 5.2)  # 变更段前读数=上段末值
        self.assertEqual(seg2["后读数"]["value"], 3.2)
        self.assertEqual(seg2["降尘效率"], 38.5)

    def test_interrupted_resumption_continues_same_segment(self):
        # 1001 第一段有 08-20 的中断补测：不另分段，备注必须说明续接
        view = self.view()
        seg1 = segment(view, 1, 1)
        self.assertEqual(seg1["段数"], 2)  # 补测不产生第三段
        self.assertTrue(any("接原段续算" in note for note in seg1["备注"]))

    def test_blank_efficiency_never_zero(self):
        view = self.view()
        # 治理前未采样（1006）
        row = segment(view, 6, 1)
        self.assertIsNone(row["前读数"]["value"])
        self.assertIsNone(row["降尘效率"])
        self.assertIn("治理前未采样", row["效率留空原因"])
        # 治理后未复测/复测缺数（1005）：效率必须留空，不能算成 0%
        row = segment(view, 5, 1)
        self.assertIsNone(row["后读数"]["value"])
        self.assertIsNone(row["降尘效率"])
        self.assertTrue(row["效率留空原因"])
        # 措施变更后尚未复测（1008 第二段）
        row = segment(view, 8, 2)
        self.assertIsNone(row["降尘效率"])
        self.assertIn("不计为零", row["效率留空原因"])
        # 前后单位不一致（1009）
        row = segment(view, 9, 1)
        self.assertIsNone(row["降尘效率"])
        self.assertIn("单位不一致", row["效率留空原因"])
        # 全部留空段统计为留空，汇总平均也不得把留空当 0
        self.assertNotIn(0.0, [
            r["降尘效率"] for g in view["groups"] for r in g["rows"]
        ])

    def test_silica_over_limit_flagged_separately(self):
        view = self.view()
        over = {item["测点编号"]: item for item in view["over_limit"]}
        # 11.5%、12.0%、13.2% 超过 10% 限值；6.5/7.2/4.8/5.1/8.4/9.0 不超
        self.assertEqual(set(over), {"DUST-1002", "DUST-1004", "DUST-1006"})
        self.assertEqual(view["summary"]["超限测点数"], 3)
        self.assertTrue(segment(view, 2, 1)["超限"])
        self.assertFalse(segment(view, 1, 1)["超限"])

    def test_legacy_backfill_by_governance_date(self):
        view = self.view()
        # 1003 存量测点按治理日期 2026-08-02 回填
        row = segment(view, 3, 1)
        self.assertTrue(row["存量回填"])
        self.assertTrue(any("2026-08-02" in note for note in row["备注"]))
        # 1004：治理日期 08-08 早于措施生效 08-10，冲突时以生效日期分段，
        # 旧区间按当时措施保留
        row = segment(view, 4, 1)
        self.assertEqual(row["区间起"], "2026-08-10")
        self.assertTrue(any("旧区间沿用当时措施" in note for note in row["备注"]))

    def test_filters_measure_and_region(self):
        spray = self.view(measure="喷雾降尘")
        self.assertEqual(
            {r["测点编号"] for g in spray["groups"] for r in g["rows"]},
            {"DUST-1001", "DUST-1002", "DUST-1003", "DUST-1006", "DUST-1008"},
        )
        # 未治理测点 1007 在按措施筛选时不出现
        self.assertNotIn(
            "DUST-1007",
            {r["测点编号"] for g in spray["groups"] for r in g["rows"]},
        )
        region = self.view(region="综采工作面")
        self.assertEqual(
            {r["测点编号"] for g in region["groups"] for r in g["rows"]},
            {"DUST-1001", "DUST-1003"},
        )

    def test_monthly_report_matches_view(self):
        report = dust_compare.build_monthly_report("2026-09")
        view = dust_compare.build_comparison(as_of=date(2026, 9, 30))
        view_rows = [r for g in view["groups"] for r in g["rows"] if not r["未治理"]]
        # 报表每一段都能在月末视图里找到，且读数/效率/原因逐字段相同
        self.assertEqual(len(report["rows"]), len(view_rows))
        indexed = {(r["point_id"], r["段序号"]): r for r in view_rows}
        for row in report["rows"]:
            same = indexed[(row["point_id"], row["段序号"])]
            self.assertEqual(row["前读数"]["value"], same["前读数"]["value"])
            self.assertEqual(row["后读数"]["value"], same["后读数"]["value"])
            self.assertEqual(row["降尘效率"], same["降尘效率"])
            self.assertEqual(row["效率留空原因"], same["效率留空原因"])
        # 8 月时点：1005、1009 尚未复测，结论应是"尚未复测"而不是其他原因
        aug = dust_compare.build_monthly_report("2026-08")
        reasons = {
            (r["point_id"], r["段序号"]): r["效率留空原因"] for r in aug["rows"]
        }
        self.assertIn("尚未复测", reasons[(5, 1)])
        self.assertIn("尚未复测", reasons[(9, 1)])

    def test_invalid_month_rejected(self):
        for bad in ("2026-9", "2026/09", "2026-13"):
            with self.assertRaises(ValueError):
                dust_compare.build_monthly_report(bad)


class DustCompareHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.seed import SEED_ROWS

        store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        self.client = TestClient(app)

    def test_endpoints(self):
        r = self.client.get("/api/dust/compare")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["summary"]["对比段数"], 11)

        r = self.client.get("/api/dust/compare", params={"measure": "煤层注水"})
        self.assertEqual(r.status_code, 200)
        # 1001、1008 都有煤层注水段；返回的段措施本身也必须全部是煤层注水
        rows = [row for g in r.json()["groups"] for row in g["rows"]]
        self.assertEqual({row["测点编号"] for row in rows}, {"DUST-1001", "DUST-1008"})
        self.assertTrue({row["降尘措施"] for row in rows} <= {"煤层注水"})

        r = self.client.get("/api/dust/monthly-report", params={"month": "2026-09"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["conclusions"]), 10)

        # 静态路径不能被 /{entry_id} 抢匹配
        self.assertEqual(self.client.get("/api/dust/export").json()["module"], "dust")
        self.assertEqual(self.client.get("/api/dust/compare", params={"as_of": "bad"}).status_code, 400)
        self.assertEqual(
            self.client.get("/api/dust/monthly-report", params={"month": "bad"}).status_code, 400
        )

    def test_internal_tables_hidden_from_overview(self):
        names = {item["name"] for item in self.client.get("/api/overview").json()["modules"]}
        self.assertIn("dust", names)
        self.assertNotIn("dust_points", names)
        self.assertNotIn("dust_samples", names)


if __name__ == "__main__":
    unittest.main()
