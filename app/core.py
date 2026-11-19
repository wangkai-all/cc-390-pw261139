"""宽表拆分/重组核心逻辑。

双写与日志重放共用这里的同一个纯函数，保证两条迁移路径产出逐字节一致：
- split:    宽表记录 -> (主记录字段, 子项分组, 子项行)
- assemble: 三类表 -> 宽表记录（split 的逆运算）

不变量：
- 子项顺序：每个子项带 seq，重组时按 seq 排序还原；
- 空数组：分组行 count=0 保留"字段存在但为空"，与"字段缺失"区分；
- 重复项：重复元素各占一行（seq 不同），不去重。
"""
import hashlib
import json


def canon(obj):
    """稳定序列化：对象键排序、数组保序、无空白。等价比较与校验和的唯一依据。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum(obj):
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def split(payload):
    """把一条宽表记录拆成三类表投影。

    顶层数组字段 -> 子项分组 + 子项行；其余（标量/对象）-> 主记录字段。
    返回 (master, groups, items)：
      master: dict，非数组顶层字段原样保留（嵌套对象不展开）；
      groups: [{"field", "count"}]，每个数组字段一行，空数组 count=0；
      items:  [{"field", "seq", "item"}]，每个元素一行，seq 为数组内序号。
    """
    if not isinstance(payload, dict):
        raise ValueError("记录必须是 JSON 对象")
    master, groups, items = {}, [], []
    for key, value in payload.items():
        if isinstance(value, list):
            groups.append({"field": key, "count": len(value)})
            for seq, element in enumerate(value):
                items.append({"field": key, "seq": seq, "item": element})
        else:
            master[key] = value
    return master, groups, items


def assemble(master, groups, items):
    """split 的逆运算：从三类表投影重组宽表记录。"""
    out = dict(master)
    for g in groups:
        out[g["field"]] = []  # 先按分组建行，空数组因此得以保留
    per_field = {}
    for it in items:
        per_field.setdefault(it["field"], []).append((it["seq"], it["item"]))
    for field, lst in per_field.items():
        out[field] = [item for _, item in sorted(lst, key=lambda t: t[0])]
    return out


def _short(value, limit=60):
    s = canon(value)
    return s if len(s) <= limit else s[:limit] + "…"


def diff_paths(old, new, path="$", out=None, limit=100):
    """逐路径对比两棵 JSON 树，返回人类可读的差异列表（数组按序对比）。"""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if canon(old) == canon(new):
        return out
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            if key not in old:
                out.append(f"{path}.{key}: 旧缺失, 新={_short(new[key])}")
            elif key not in new:
                out.append(f"{path}.{key}: 旧={_short(old[key])}, 新缺失")
            else:
                diff_paths(old[key], new[key], f"{path}.{key}", out, limit)
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            if i >= len(old):
                out.append(f"{path}[{i}]: 旧缺失, 新={_short(new[i])}")
            elif i >= len(new):
                out.append(f"{path}[{i}]: 旧={_short(old[i])}, 新缺失")
            else:
                diff_paths(old[i], new[i], f"{path}[{i}]", out, limit)
    else:
        out.append(f"{path}: 旧={_short(old)}, 新={_short(new)}")
    return out
