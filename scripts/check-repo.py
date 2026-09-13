#!/usr/bin/env python3
"""Проверяет каркас репозитория, не изменяя его. Python 3.9+, без зависимостей.

Не проверяет продукт, внешние ссылки, Markdown-якоря или содержимое records/.
Поддерживает простые Markdown-ссылки [текст](путь) без пробелов в пути.
Это вспомогательная проверка шаблона, а не оригинальный инструмент Рената.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from typing import List, Optional
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FILES = (
    "AGENTS.md", "CLAUDE.md", "README.md",
    "docs/how-to-document.md",
    "docs/development.md", "docs/workflow.md",
)
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^\s()]+)\)")
FIELD = re.compile(r"\{\{[A-Z_]+\}\}")


class Audit:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.documents = 0

    def local_path(self, path: Path, label: str) -> Optional[Path]:
        """Запрещает выход за корень и переходы по символическим ссылкам."""
        normalized = Path(os.path.abspath(path))
        try:
            parts = normalized.relative_to(ROOT).parts
        except ValueError:
            self.errors.append(f"{label}: путь за пределами репозитория.")
            return None
        # Даже явные ссылки на историю не открываются и не проверяются.
        if parts and parts[0] == "records":
            return None
        current = ROOT
        for part in parts:
            current = current / part
            if current.is_symlink():
                self.errors.append(f"{label}: символическая ссылка вместо локального пути.")
                return None
        return normalized

    def read(self, path: Path) -> Optional[str]:
        label = path.relative_to(ROOT).as_posix()
        safe = self.local_path(path, label)
        if safe is None:
            return None
        try:
            if not safe.is_file():
                self.errors.append(f"{label}: ожидается обычный текстовый файл.")
                return None
            raw = safe.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            self.errors.append(f"{label}: не удалось прочитать обычный файл UTF-8.")
            return None
        if b"\r" in raw:
            self.errors.append(f"{label}: нужны переводы строк LF.")
        if raw and not raw.endswith(b"\n"):
            self.errors.append(f"{label}: отсутствует завершающий перевод строки.")
        return text

    def check_links(self, path: Path, text: str) -> None:
        # Проверяются ссылки вне блоков кода; якоря намеренно не проверяются.
        fence = ""
        for number, line in enumerate(text.splitlines(), 1):
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)
                if not fence:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = ""
                continue
            if fence:
                continue
            for match in LINK.finditer(line):
                label = f"{path.relative_to(ROOT).as_posix()}:{number}"
                target = match.group(1).strip("<>")
                try:
                    url = urlsplit(target)
                    if url.scheme or url.netloc or not url.path:
                        continue
                    resolved = self.local_path(path.parent / unquote(url.path), label)
                    if resolved is not None and not resolved.exists():
                        self.errors.append(f"{label}: локальная ссылка ведёт в отсутствующий путь.")
                except (OSError, ValueError):
                    self.errors.append(f"{label}: некорректная локальная ссылка.")

    def scan_docs(self) -> List[Path]:
        result: List[Path] = []
        docs = self.local_path(ROOT / "docs", "docs/")
        if docs is None or not docs.is_dir():
            self.errors.append("docs/: отсутствует обычный каталог документации.")
            return result

        def on_error(error: OSError) -> None:
            self.errors.append("docs/: не удалось прочитать часть дерева документов.")

        for folder, directories, filenames in os.walk(docs, followlinks=False, onerror=on_error):
            for name in list(directories):
                child = Path(folder) / name
                if child.is_symlink():
                    self.errors.append(f"{child.relative_to(ROOT)}: ссылка не обходится.")
                    directories.remove(name)
            for name in filenames:
                child = Path(folder) / name
                if child.is_symlink():
                    self.errors.append(f"{child.relative_to(ROOT)}: ссылка не читается.")
                elif child.suffix.lower() == ".md":
                    result.append(child)
        return result

    def run(self) -> None:
        for relative in REQUIRED_FILES:
            safe = self.local_path(ROOT / relative, relative)
            if safe is not None and not safe.is_file():
                self.errors.append(f"{relative}: отсутствует обязательный файл шаблона.")
        records = ROOT / "records"
        # Проверяется только сам каталог, не список файлов и не их содержимое.
        if records.is_symlink() or not records.is_dir():
            self.errors.append("records/: отсутствует обычный каталог истории.")
        paths = {ROOT / name for name in ("README.md", "AGENTS.md", "CLAUDE.md")}
        paths.update(self.scan_docs())
        for path in sorted(paths):
            text = self.read(path)
            if text is None:
                continue
            self.documents += 1
            self.check_links(path, text)
            if path.name == "AGENTS.md" and path.parent == ROOT:
                if FIELD.search(text):
                    self.warnings.append("AGENTS.md: заполните название и назначение проекта.")
            if path.name == "CLAUDE.md" and path.parent == ROOT:
                if text.strip() != "@AGENTS.md":
                    self.errors.append("CLAUDE.md: ожидается только импорт @AGENTS.md без копии правил.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Завершаться с ошибкой также при предупреждениях.")
    args = parser.parse_args()
    audit = Audit()
    try:
        audit.run()
    except (OSError, ValueError):
        audit.errors.append("Проверка прервана: не удалось безопасно прочитать структуру репозитория.")
    print(f"Проверено Markdown-файлов: {audit.documents}.")
    for message in audit.errors:
        print("ОШИБКА: " + message)
    for message in audit.warnings:
        print("ПРЕДУПРЕЖДЕНИЕ: " + message)
    if audit.errors or (args.strict and audit.warnings):
        print("Целостность шаблона не подтверждена в выбранном режиме.")
        return 1
    print("Ошибок структуры не обнаружено. Продукт и его окружение не проверялись.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
