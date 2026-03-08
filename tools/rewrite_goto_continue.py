#!/usr/bin/env python3
"""Rewrite Lua goto-continue patterns into repeat/break blocks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


@dataclass(frozen=True)
class Token:
    type: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class LoopBlock:
    body_start: int
    body_end: int


@dataclass(frozen=True)
class LabelToken:
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class GotoToken:
    name: str
    start: int
    end: int


def read_file_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_file_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def read_long_bracket(text: str, start: int) -> int | None:
    if text[start] != "[":
        return None
    i = start + 1
    while i < len(text) and text[i] == "=":
        i += 1
    if i >= len(text) or text[i] != "[":
        return None
    eq_count = i - (start + 1)
    close = "]" + ("=" * eq_count) + "]"
    end = text.find(close, i + 1)
    if end == -1:
        return len(text)
    return end + len(close)


def read_short_string(text: str, start: int) -> int:
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return len(text)


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            if i + 2 < n and text[i + 2] == "[":
                long_end = read_long_bracket(text, i + 2)
                if long_end is not None:
                    i = long_end
                    continue
            line_end = text.find("\n", i + 2)
            if line_end == -1:
                return tokens
            i = line_end + 1
            continue
        if ch in ("\"", "'"):
            i = read_short_string(text, i)
            continue
        if ch == "[":
            long_end = read_long_bracket(text, i)
            if long_end is not None:
                i = long_end
                continue
        if ch == ":" and i + 1 < n and text[i + 1] == ":":
            tokens.append(Token("symbol", "::", i, i + 2))
            i += 2
            continue
        if ch.isalpha() or ch == "_":
            start = i
            i += 1
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            value = text[start:i]
            tokens.append(Token("name", value, start, i))
            continue
        tokens.append(Token("symbol", ch, i, i + 1))
        i += 1
    return tokens


def find_labels(tokens: list[Token]) -> list[LabelToken]:
    labels: list[LabelToken] = []
    for idx, token in enumerate(tokens[:-2]):
        if token.type == "symbol" and token.value == "::":
            name_token = tokens[idx + 1]
            end_token = tokens[idx + 2]
            if name_token.type == "name" and end_token.type == "symbol" and end_token.value == "::":
                labels.append(LabelToken(name_token.value, token.start, end_token.end))
    return labels


def find_gotos(tokens: list[Token]) -> list[GotoToken]:
    gotos: list[GotoToken] = []
    for idx, token in enumerate(tokens[:-1]):
        if token.type == "name" and token.value == "goto":
            name_token = tokens[idx + 1]
            if name_token.type == "name":
                gotos.append(GotoToken(name_token.value, token.start, name_token.end))
    return gotos


def find_loops(tokens: list[Token]) -> list[LoopBlock]:
    loops: list[LoopBlock] = []
    stack: list[tuple[str, int, bool]] = []
    pending_loop = False

    for token in tokens:
        if token.type == "name" and token.value in ("for", "while"):
            pending_loop = True
            continue
        if token.type == "name" and token.value == "repeat":
            stack.append(("until", token.end, True))
            continue
        if token.type == "name" and token.value == "do":
            if pending_loop:
                stack.append(("end", token.end, True))
                pending_loop = False
            else:
                stack.append(("end", token.end, False))
            continue
        if token.type == "name" and token.value in ("function", "if"):
            stack.append(("end", token.end, False))
            continue
        if token.type == "name" and token.value == "end":
            while stack:
                end_kind, start_pos, is_loop = stack.pop()
                if end_kind == "end":
                    if is_loop:
                        loops.append(LoopBlock(start_pos, token.start))
                    break
            continue
        if token.type == "name" and token.value == "until":
            while stack:
                end_kind, start_pos, is_loop = stack.pop()
                if end_kind == "until":
                    if is_loop:
                        loops.append(LoopBlock(start_pos, token.start))
                    break
            continue

    return loops


def line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def line_end(text: str, pos: int) -> int:
    end = text.find("\n", pos)
    if end == -1:
        return len(text)
    return end


def next_non_empty_line_start(text: str, pos: int) -> int | None:
    i = pos
    if i > 0 and text[i - 1] != "\n":
        next_break = text.find("\n", i)
        if next_break == -1:
            return None
        i = next_break + 1
    while i < len(text):
        end = text.find("\n", i)
        if end == -1:
            end = len(text)
        line = text[i:end]
        if line.strip():
            return i
        i = end + 1
    return None


def previous_non_empty_line_start(text: str, pos: int) -> int | None:
    i = pos
    if i > 0 and text[i - 1] == "\n":
        i -= 1
    while i >= 0:
        start = text.rfind("\n", 0, i) + 1
        end = text.find("\n", start)
        if end == -1:
            end = len(text)
        line = text[start:end]
        if line.strip():
            return start
        if start == 0:
            break
        i = start - 1
    return None


def line_indent(line: str) -> str:
    stripped = line.lstrip(" \t")
    return line[: len(line) - len(stripped)]


def find_innermost_loop(loops: list[LoopBlock], pos: int) -> LoopBlock | None:
    containing = [loop for loop in loops if loop.body_start <= pos < loop.body_end]
    if not containing:
        return None
    return min(containing, key=lambda loop: loop.body_end - loop.body_start)


def build_edits(text: str, loops: list[LoopBlock], labels: list[LabelToken], gotos: list[GotoToken]) -> list[tuple[int, int, str]]:
    edits: list[tuple[int, int, str]] = []
    loops_sorted = sorted(loops, key=lambda loop: loop.body_start, reverse=True)

    labels_by_loop: dict[LoopBlock, list[LabelToken]] = {}
    gotos_by_loop: dict[LoopBlock, list[GotoToken]] = {}

    for label in labels:
        if label.name != "continue":
            continue
        loop = find_innermost_loop(loops, label.start)
        if loop is None:
            continue
        labels_by_loop.setdefault(loop, []).append(label)

    for goto in gotos:
        if goto.name != "continue":
            continue
        loop = find_innermost_loop(loops, goto.start)
        if loop is None:
            continue
        gotos_by_loop.setdefault(loop, []).append(goto)

    for loop in loops_sorted:
        labels_in_loop = labels_by_loop.get(loop, [])
        gotos_in_loop = gotos_by_loop.get(loop, [])
        if not labels_in_loop or not gotos_in_loop:
            continue

        first_line_pos = next_non_empty_line_start(text, loop.body_start)
        if first_line_pos is not None:
            first_line_end = line_end(text, first_line_pos)
            first_line = text[first_line_pos:first_line_end]
            if first_line.strip() != "repeat":
                indent = line_indent(first_line)
                edits.append((first_line_pos, first_line_pos, f"{indent}repeat\n"))

        end_line_pos = line_start(text, loop.body_end)
        previous_line_pos = previous_non_empty_line_start(text, end_line_pos)
        previous_line = ""
        if previous_line_pos is not None:
            previous_line_end = line_end(text, previous_line_pos)
            previous_line = text[previous_line_pos:previous_line_end]
        if previous_line.strip() != "until true":
            indent = ""
            if first_line_pos is not None:
                first_line_end = line_end(text, first_line_pos)
                first_line = text[first_line_pos:first_line_end]
                indent = line_indent(first_line)
            edits.append((end_line_pos, end_line_pos, f"{indent}until true\n"))

        for goto in gotos_in_loop:
            edits.append((goto.start, goto.end, "break"))

        for label in labels_in_loop:
            label_line_start = line_start(text, label.start)
            label_line_end = line_end(text, label.start)
            label_line = text[label_line_start:label_line_end]
            if label_line.strip() == "::continue::":
                cut_end = label_line_end
                if cut_end < len(text) and text[cut_end:cut_end + 1] == "\n":
                    cut_end += 1
                edits.append((label_line_start, cut_end, ""))
            else:
                edits.append((label.start, label.end, ""))

    return edits


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    if not edits:
        return text
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def rewrite_text(text: str) -> str:
    tokens = tokenize(text)
    labels = find_labels(tokens)
    gotos = find_gotos(tokens)
    loops = find_loops(tokens)
    edits = build_edits(text, loops, labels, gotos)
    return apply_edits(text, edits)


def collect_goto_tokens(text: str) -> list[GotoToken]:
    tokens = tokenize(text)
    return find_gotos(tokens)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    game_root = repo_root / "game"
    lua_files = sorted(game_root.rglob("*.lua"))

    changed = 0
    for path in lua_files:
        original = read_file_text(path)
        rewritten = rewrite_text(original)
        if rewritten != original:
            write_file_text(path, rewritten)
            changed += 1

    remaining = []
    for path in lua_files:
        text = read_file_text(path)
        gotos = collect_goto_tokens(text)
        if gotos:
            remaining.append(path)

    if remaining:
        print("goto statements remain after rewrite:")
        for path in remaining:
            print(f" - {path}")
        return 1

    print(f"Rewrote goto-continue in {changed} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
