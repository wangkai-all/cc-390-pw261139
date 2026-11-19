"""核心拆分/重组不变量单元测试：python3 tests/run_tests.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import core  # noqa: E402


class SplitAssembleRoundTrip(unittest.TestCase):
    CASES = {
        "普通嵌套": {
            "id": "r1", "pv": 10,
            "hourly": [{"hour": 0, "pv": 3}, {"hour": 1, "pv": 5}],
            "attr": {"model": "last_click"},
        },
        "空数组保留": {"id": "r2", "top_pages": [], "pv": 1},
        "字段缺失与空数组不同": {"id": "r3", "pv": 1},
        "重复子项": {"id": "r4", "rows": [{"a": 1}, {"a": 1}, {"a": 1}]},
        "重复标量子项": {"id": "r5", "tags": ["x", "x", "y", "x"]},
        "子项内再嵌套数组": {"id": "r6", "segs": [{"dims": ["ios", "android"], "kv": {"a": [1, 2]}}]},
        "数组元素为数组": {"id": "r7", "matrix": [[1, 2], [3, 4]]},
        "空对象与空记录": {"id": "r8", "meta": {}, "list": [{}]},
        "中文与特殊字符": {"id": "r9", "渠道": "移动端", "备注": "含\"引号\"与\n换行"},
        "数值类型": {"id": "r10", "int": 1, "float": 1.5, "bool": True, "null": None},
        "只有id": {"id": "r11"},
    }

    def test_roundtrip(self):
        for name, payload in self.CASES.items():
            with self.subTest(case=name):
                master, groups, items = core.split(payload)
                rebuilt = core.assemble(master, groups, items)
                self.assertEqual(core.canon(rebuilt), core.canon(payload))

    def test_checksum_stable(self):
        for name, payload in self.CASES.items():
            with self.subTest(case=name):
                self.assertEqual(core.checksum(payload), core.checksum(payload))


class Invariants(unittest.TestCase):
    def test_order_preserved(self):
        payload = {"id": "o1", "rows": [{"n": i} for i in range(50)]}
        _, _, items = core.split(payload)
        self.assertEqual([it["seq"] for it in items], list(range(50)))
        self.assertEqual([it["item"]["n"] for it in items], list(range(50)))

    def test_duplicates_not_deduped(self):
        payload = {"id": "d1", "rows": [{"a": 1}, {"a": 1}]}
        _, groups, items = core.split(payload)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0]["seq"], items[1]["seq"])

    def test_empty_array_has_group_row(self):
        _, groups, items = core.split({"id": "e1", "empty": []})
        self.assertEqual(groups, [{"field": "empty", "count": 0}])
        self.assertEqual(items, [])

    def test_absent_field_has_no_group(self):
        _, groups, _ = core.split({"id": "e2"})
        self.assertEqual(groups, [])

    def test_empty_vs_absent_after_assemble(self):
        with_empty = core.assemble(*core.split({"id": "x", "empty": []}))
        without = core.assemble(*core.split({"id": "x"}))
        self.assertIn("empty", with_empty)
        self.assertNotIn("empty", without)

    def test_split_rejects_non_object(self):
        with self.assertRaises(ValueError):
            core.split([1, 2, 3])


class CanonAndDiff(unittest.TestCase):
    def test_canon_key_order_insensitive(self):
        a = {"x": 1, "y": [1, 2], "z": {"b": 2, "a": 1}}
        b = {"z": {"a": 1, "b": 2}, "y": [1, 2], "x": 1}
        self.assertEqual(core.canon(a), core.canon(b))

    def test_canon_array_order_sensitive(self):
        self.assertNotEqual(core.canon({"a": [1, 2]}), core.canon({"a": [2, 1]}))

    def test_bool_not_equal_int(self):
        self.assertNotEqual(core.canon({"a": True}), core.canon({"a": 1}))

    def test_diff_paths_finds_leaf(self):
        old = {"a": {"b": 1}, "arr": [1, 2, 3]}
        new = {"a": {"b": 2}, "arr": [1, 2]}
        diffs = core.diff_paths(old, new)
        self.assertTrue(any("$.a.b" in d for d in diffs))
        self.assertTrue(any("$.arr[2]" in d for d in diffs))

    def test_diff_paths_equal_is_empty(self):
        self.assertEqual(core.diff_paths({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
