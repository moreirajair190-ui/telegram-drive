from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .utils import extract_hashtags


@dataclass
class MenuNode:
    title: str
    level: int = 0
    tags: list[str] = field(default_factory=list)
    children: list["MenuNode"] = field(default_factory=list)

    def has_content(self) -> bool:
        return bool(self.tags or self.children)


def parse_menu(text: str | None) -> MenuNode:
    """Parseia menus do Telegram no formato hierárquico:

        = Módulo
        == Aula
        === Tipo
        #TAG01 #TAG02

    Tolerante a sumários simples; mantém as hashtags na seção anterior.
    """
    root = MenuNode("Menu do curso", level=0)
    if not text:
        return root

    stack: list[MenuNode] = [root]
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("="):
            level = 0
            for ch in line:
                if ch == "=":
                    level += 1
                else:
                    break
            title = line[level:].strip(" =\t")
            if not title:
                continue
            while stack and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1] if stack else root
            node = MenuNode(title=title, level=level)
            parent.children.append(node)
            stack.append(node)
            continue

        tags = extract_hashtags(line)
        if tags:
            target = stack[-1] if len(stack) > 1 else root
            for tag in tags:
                if tag not in target.tags:
                    target.tags.append(tag)

    return root


def iter_nodes(node: MenuNode) -> Iterable[MenuNode]:
    yield node
    for child in node.children:
        yield from iter_nodes(child)


def count_tags(node: MenuNode) -> int:
    return sum(len(n.tags) for n in iter_nodes(node))


def heading_count(text: str | None) -> int:
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if line.strip().startswith("="))


def menu_score(text: str | None) -> int:
    if not text:
        return 0
    tags = extract_hashtags(text)
    headings = heading_count(text)
    lowered = text.lower()
    menu_words = sum(
        1
        for word in ("menu", "sumário", "sumario", "videoaula", "miniaula", "bônus", "bonus")
        if word in lowered
    )
    return len(tags) * 8 + headings * 7 + menu_words * 10 + min(len(text), 12000) // 150


def looks_like_menu(text: str | None) -> bool:
    if not text:
        return False
    clean = text.strip()
    if not clean:
        return False
    tags = extract_hashtags(clean)
    headings = heading_count(clean)
    lowered = clean.lower()
    has_menu_word = any(
        word in lowered
        for word in ("menu", "sumário", "sumario", "videoaula", "miniaula", "bônus", "bonus")
    )
    if headings >= 1 and len(tags) >= 2:
        return True
    if headings >= 2:
        return True
    if len(tags) >= 8 and has_menu_word:
        return True
    if len(tags) >= 12 and len(clean.splitlines()) >= 3:
        return True
    return False


def first_heading(text: str | None) -> str | None:
    if not text:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("="):
            title = line.lstrip("=").strip(" =\t")
            if title:
                return title
    return None


def tag_prefix(text: str | None) -> str | None:
    tags = extract_hashtags(text)
    if not tags:
        return None
    tag = tags[0].lstrip("#")
    prefix = ""
    for ch in tag:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix or tag


def all_tag_prefixes(text: str | None) -> list[str]:
    prefixes: list[str] = []
    for tag in extract_hashtags(text):
        clean = tag.lstrip("#")
        prefix = ""
        for ch in clean:
            if ch.isalpha():
                prefix += ch
            else:
                break
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def derive_menu_title(text: str | None, fallback: str = "Sumário") -> str:
    heading = first_heading(text)
    prefix = tag_prefix(text)
    if heading and prefix:
        if f"#{prefix}".lower() in heading.lower():
            return heading
        return f"{heading} · #{prefix}"
    if heading:
        return heading
    if prefix:
        return f"Menu #{prefix}"
    return fallback


def split_summary_candidates(text: str | None) -> list[tuple[str, str]]:
    """Divide uma mensagem de menu grande em sumários menores por matéria."""
    if not text:
        return []
    lines = text.splitlines()
    preface: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        is_top_heading = bool(re.match(r"^=([^=].*)$", stripped))
        if is_top_heading:
            if current_lines:
                blocks.append(
                    (current_title or derive_menu_title("\n".join(current_lines)), current_lines)
                )
            current_title = stripped.lstrip("=").strip(" =\t") or "Sumário"
            current_lines = [line]
        else:
            if current_lines:
                current_lines.append(line)
            else:
                preface.append(line)
    if current_lines:
        blocks.append(
            (current_title or derive_menu_title("\n".join(current_lines)), current_lines)
        )

    if len(blocks) <= 1:
        return [(derive_menu_title(text), text)] if looks_like_menu(text) else []

    result: list[tuple[str, str]] = []
    for title, block_lines in blocks:
        block_text = "\n".join([*preface, *block_lines]).strip()
        tags = extract_hashtags(block_text)
        if len(tags) < 2 and heading_count(block_text) < 1:
            continue
        result.append((derive_menu_title(block_text, title), block_text))

    useful = [item for item in result if len(extract_hashtags(item[1])) >= 2]
    if len(useful) >= 2:
        return useful
    return [(derive_menu_title(text), text)]


def compact_summary_text(topics: list[dict]) -> str | None:
    if not topics:
        return None
    parts: list[str] = []
    for topic in topics:
        title = topic.get("title") or "Sumário"
        text = topic.get("summary_text") or ""
        parts.append(f"===== {title} =====\n{text}".strip())
    return "\n\n".join(parts).strip() or None
