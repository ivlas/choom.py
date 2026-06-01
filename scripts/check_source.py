import argparse
import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE_LIMIT = int(os.getenv("SOURCE_LINE_LIMIT", "700"))
COMPLEXITY_LIMIT = int(os.getenv("SOURCE_COMPLEXITY_LIMIT", "12"))


@dataclass
class FunctionComplexity:
    path: Path
    name: str
    line: int
    complexity: int


@dataclass
class SourceFile:
    path: Path
    text: str
    tree: ast.Module


class ComplexityCounter(ast.NodeVisitor):
    def __init__(self) -> None:
        self.value = 1

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.value += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.value += max(0, len(node.cases) - 1)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.value += sum(len(generator.ifs) for generator in node.generators)
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.value += sum(len(generator.ifs) for generator in node.generators)
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.value += sum(len(generator.ifs) for generator in node.generators)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.value += sum(len(generator.ifs) for generator in node.generators)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return


def source_paths(names: list[str]) -> list[Path]:
    paths = []

    for name in names:
        path = ROOT / name
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(sorted(path.rglob("*.py")))

    return paths


def source_files(names: list[str]) -> list[SourceFile]:
    files = []
    for path in source_paths(names):
        text = path.read_text()
        files.append(SourceFile(path, text, ast.parse(text, filename=str(path))))
    return files


def source_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    counter = ComplexityCounter()
    for child in node.body:
        counter.visit(child)
    return counter.value


def complex_functions(source: SourceFile) -> list[FunctionComplexity]:
    functions = []

    for node in ast.walk(source.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(
                FunctionComplexity(
                    path=source.path,
                    name=node.name,
                    line=node.lineno,
                    complexity=function_complexity(node),
                )
            )

    return functions


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=["choom.py", "choom"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    files = source_files(args.paths)
    if not files:
        print("source check failed: no source files found", file=sys.stderr)
        return 1

    total_lines = sum(source_lines(source.text) for source in files)
    functions = [item for source in files for item in complex_functions(source)]
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
            print(
                f"  {relative}:{item.line} {item.name} complexity={item.complexity}",
                file=sys.stderr,
            )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
