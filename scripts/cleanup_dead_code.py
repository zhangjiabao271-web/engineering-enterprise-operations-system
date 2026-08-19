"""清理 database.py 中"先 return service 委派、后跟死代码"的函数体。

检测模式：函数体内最后一个**顶层** return 语句之后若还有其他语句，
则这些语句为不可达死代码（Python 顶层 return 后的语句永不执行）。
删除该 return 之后的所有语句，保留 return 之前可能存在的活代码。

用法：python scripts/cleanup_dead_code.py [--apply]
不带 --apply 时只输出检测结果与建议删除范围（dry-run）。
"""
import ast
import sys


def find_dead_ranges(source):
    """返回 [(start_line, end_line, func_name, hint), ...] 需删除的行范围（1 基，含端）。"""
    tree = ast.parse(source)
    ranges = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        # 跳过 docstring
        idx = 0
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            idx = 1
        if idx >= len(body):
            continue
        # 找第一个顶层 return（顶层 return 之后的语句必然不可达）
        first_return = None
        for i in range(idx, len(body)):
            if isinstance(body[i], ast.Return):
                first_return = i
                break
        if first_return is None or first_return >= len(body) - 1:
            continue
        # return 之后的语句均为死代码；注意 return 可能是多行表达式
        # （如 return sorted(...)），必须从 return 语句的 end_lineno 之后开始删
        start = body[first_return].end_lineno + 1
        end = node.body[-1].end_lineno
        if end >= start:
            hint = "委派" if isinstance(body[first_return].value, ast.Call) else "提前返回"
            ranges.append((start, end, node.name, hint))
    return ranges


def main():
    apply = "--apply" in sys.argv
    path = "database.py"
    with open(path, encoding="utf-8") as f:
        source = f.read()
    lines = source.splitlines(keepends=True)
    ranges = find_dead_ranges(source)
    ranges.sort()

    total = 0
    print(f"发现 {len(ranges)} 段死代码：")
    for start, end, name, hint in ranges:
        dead = end - start + 1
        total += dead
        print(f"  {name}(): 行 {start}-{end}（{dead} 行）→ {hint}")
    print(f"合计死代码 {total} 行")

    if not apply:
        print("\n[dry-run] 未修改文件。确认后加 --apply 执行。")
        return

    # 从后往前删除，避免行号偏移
    for start, end, name, hint in reversed(ranges):
        del lines[start - 1:end]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"\n已删除 {total} 行死代码，文件已更新。")


if __name__ == "__main__":
    main()
