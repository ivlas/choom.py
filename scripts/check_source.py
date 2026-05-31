import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE_LIMIT = int(os.getenv("SOURCE_LINE_LIMIT", "700"))
COMPLEXITY_LIMIT = int(os.getenv("SOURCE_COMPLEXITY_LIMIT", "16"))


@dataclass
class FunctionComplexity:
    path: Path
    name: str
    line: int
    complexity: int


def app_source_paths() -> list[Path]:
    paths = []
    single_file = ROOT / "choom.py"
    package_dir = ROOT / "choom"

    if single_file.exists():
        paths.append(single_file)
    if package_dir.exists():
        paths.extend(sorted(package_dir.rglob("*.py")))

    return paths


def source_lines(path: Path) -> int:
    count = 0
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def decision_points(node: ast.AST) -> int:
    if isinstance(node, ast.BoolOp):
        return max(0, len(node.values) - 1)
    if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
        return 1
    if isinstance(node, ast.Match):
        return max(0, len(node.cases) - 1)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return sum(len(generator.ifs) for generator in node.generators)
    return 0


def function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return 1 + sum(decision_points(child) for child in ast.walk(node))


def complex_functions(path: Path) -> list[FunctionComplexity]:
    tree = ast.parse(path.read_text(), filename=str(path))
    functions = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(
                FunctionComplexity(
                    path=path,
                    name=node.name,
                    line=node.lineno,
                    complexity=function_complexity(node),
                )
            )

    return functions


def main() -> int:
    paths = app_source_paths()
    if not paths:
        print("source check failed: no source files found", file=sys.stderr)
        return 1

    total_lines = sum(source_lines(path) for path in paths)
    functions = [item for path in paths for item in complex_functions(path)]
    too_complex = [item for item in functions if item.complexity > COMPLEXITY_LIMIT]

    print(f"app source lines: {total_lines} / {LINE_LIMIT}")
    print(f"max complexity: {max((item.complexity for item in functions), default=0)} / {COMPLEXITY_LIMIT}")

    if total_lines > LINE_LIMIT:
        print(f"source check failed: {total_lines} source lines exceeds {LINE_LIMIT}", file=sys.stderr)
        return 1

    if too_complex:
        print("source check failed: functions over complexity limit:", file=sys.stderr)
        for item in sorted(too_complex, key=lambda value: value.complexity, reverse=True):
            relative = item.path.relative_to(ROOT)
            print(f"  {relative}:{item.line} {item.name} complexity={item.complexity}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
