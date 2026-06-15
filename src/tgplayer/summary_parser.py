"""Parser de SUMÁRIO de uma matéria.

Formato hierárquico esperado no sumário de CADA matéria:

    = Módulo
    == Aula
    === Tipo (Videoaula / Resumo / Bônus / ...)
    #TAG01 #TAG02     (hashtags que LIGAM o item do menu ao vídeo)

Conceitos importantes (corrigindo bugs anteriores):
- Cada matéria tem o SEU PRÓPRIO sumário. Este módulo NÃO agrupa por prefixo de
  hashtag global e NÃO mistura sumários entre matérias.
- O resultado é uma ÁRVORE Módulo -> Aula -> Tipo -> [hashtags].
- Texto decorativo é ignorado (ex.: "⚠️ Atenção ⚠️", "Clique aqui para ver o
  Menu", linhas de enfeite).
- `match_videos_to_tree` casa cada hashtag do sumário com o vídeo que tem a
  MESMA hashtag (na legenda, nome do arquivo ou texto). Vídeos sem match vão
  para "Sem módulo".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .utils import extract_hashtags

# Linhas que são claramente decoração e devem ser ignoradas pelo parser.
_DECOR_PATTERNS = [
    r"clique aqui",
    r"clique no",
    r"ver o menu",
    r"voltar ao menu",
    r"aten[cç][aã]o",
    r"^[\W_]+$",  # só símbolos/emojis
    r"^[-=_~·•—\s]+$",
]
_DECOR_RE = re.compile("|".join(_DECOR_PATTERNS), re.IGNORECASE)

_MENU_WORDS = (
    "menu", "sumário", "sumario", "índice", "indice", "videoaula", "video aula",
    "miniaula", "mini aula", "resumo", "bônus", "bonus", "módulo", "modulo", "aula",
)


@dataclass
class MenuNode:
    title: str
    level: int = 0  # 1=Módulo, 2=Aula, 3=Tipo
    tags: list[str] = field(default_factory=list)
    children: list["MenuNode"] = field(default_factory=list)

    def has_content(self) -> bool:
        return bool(self.tags or any(c.has_content() for c in self.children))


def _is_decoration(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return True
    # Linhas só com hashtags NÃO são decoração.
    if extract_hashtags(clean) and not re.sub(r"#[\wÀ-ÿ]+", "", clean).strip():
        return False
    return bool(_DECOR_RE.search(clean))


def _heading_level(line: str) -> tuple[int, str] | None:
    """Se a linha for um cabeçalho `= ...`, retorna (nível, título)."""
    stripped = line.strip()
    if not stripped.startswith("="):
        return None
    level = 0
    for ch in stripped:
        if ch == "=":
            level += 1
        else:
            break
    title = stripped[level:].strip(" =\t·•—-")
    if not title:
        return None
    return level, title


def parse_summary(text: str | None, root_title: str = "Sumário") -> MenuNode:
    """Parseia o sumário de UMA matéria numa árvore Módulo->Aula->Tipo->tags."""
    root = MenuNode(root_title, level=0)
    if not text:
        return root

    stack: list[MenuNode] = [root]
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        heading = _heading_level(line)
        if heading:
            level, title = heading
            # também guarda hashtags que estejam na MESMA linha do cabeçalho
            inline_tags = extract_hashtags(title)
            if inline_tags:
                title = re.sub(r"#[\wÀ-ÿ]+", "", title).strip(" -·•—") or title
            while stack and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1] if stack else root
            node = MenuNode(title=title, level=level)
            parent.children.append(node)
            stack.append(node)
            for tag in inline_tags:
                if tag not in node.tags:
                    node.tags.append(tag)
            continue

        if _is_decoration(line):
            continue

        tags = extract_hashtags(line)
        if tags:
            target = stack[-1]
            # Se ainda estamos no root (sumário sem cabeçalhos), cria um nó
            # genérico para não perder as hashtags.
            if target is root:
                if not root.children or root.children[-1].level != 1:
                    generic = MenuNode("Aulas", level=1)
                    root.children.append(generic)
                    stack = [root, generic]
                target = stack[-1]
            for tag in tags:
                if tag not in target.tags:
                    target.tags.append(tag)
            continue

        # Linha de texto sem hashtag e sem `=`: ignorada para não poluir a
        # árvore (texto solto/descrições não fazem parte da estrutura).
    return root


# Compatibilidade com nome antigo usado em outros módulos.
def parse_menu(text: str | None) -> MenuNode:
    return parse_summary(text, "Menu do curso")


def iter_nodes(node: MenuNode) -> Iterable[MenuNode]:
    yield node
    for child in node.children:
        yield from iter_nodes(child)


def count_tags(node: MenuNode) -> int:
    return sum(len(n.tags) for n in iter_nodes(node))


def all_tags(node: MenuNode) -> list[str]:
    out: list[str] = []
    for n in iter_nodes(node):
        for tag in n.tags:
            if tag not in out:
                out.append(tag)
    return out


def heading_count(text: str | None) -> int:
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if _heading_level(line))


def menu_score(text: str | None) -> int:
    """Pontua o quanto um texto 'parece um menu/sumário'."""
    if not text:
        return 0
    tags = extract_hashtags(text)
    headings = heading_count(text)
    lowered = text.lower()
    menu_words = sum(1 for word in _MENU_WORDS if word in lowered)
    return len(tags) * 8 + headings * 7 + menu_words * 6 + min(len(text), 12000) // 200


def looks_like_menu(text: str | None) -> bool:
    """Heurística para decidir se uma mensagem é um sumário/menu da matéria."""
    if not text:
        return False
    clean = text.strip()
    if not clean:
        return False
    tags = extract_hashtags(clean)
    headings = heading_count(clean)
    lowered = clean.lower()
    has_menu_word = any(word in lowered for word in _MENU_WORDS)
    if headings >= 1 and len(tags) >= 1:
        return True
    if headings >= 2:
        return True
    if len(tags) >= 6 and has_menu_word:
        return True
    if len(tags) >= 10 and len(clean.splitlines()) >= 3:
        return True
    return False


def first_heading(text: str | None) -> str | None:
    if not text:
        return None
    for raw_line in text.splitlines():
        heading = _heading_level(raw_line)
        if heading:
            return heading[1]
    return None


# ----------------------------------------------------------- casamento com vídeos
def _normalize_tag(tag: str) -> str:
    return "#" + tag.lstrip("#").upper()


def build_tag_index(videos: Iterable) -> dict[str, list]:
    """Mapeia cada hashtag (maiúsculas) -> lista de vídeos que a contêm."""
    index: dict[str, list] = {}
    for video in videos:
        seen: set[str] = set()
        for tag in getattr(video, "hashtags", []) or []:
            norm = _normalize_tag(tag)
            if norm in seen:
                continue
            seen.add(norm)
            index.setdefault(norm, []).append(video)
    return index


@dataclass
class MatchResult:
    tree: MenuNode
    matched_ids: set[int]


def match_videos_to_tree(
    summary_text: str | None,
    videos: list,
    root_title: str = "Sumário",
) -> MatchResult:
    """Constrói a árvore do sumário e marca quais vídeos foram casados.

    Não altera os vídeos; apenas devolve o conjunto de ids casados para que a
    UI saiba quais ficam em "Sem módulo".
    """
    tree = parse_summary(summary_text, root_title)
    index = build_tag_index(videos)
    matched_ids: set[int] = set()
    for node in iter_nodes(tree):
        for tag in node.tags:
            for video in index.get(_normalize_tag(tag), []):
                matched_ids.add(int(getattr(video, "id", 0)))
    return MatchResult(tree=tree, matched_ids=matched_ids)


# ------------------------------------------------- divisão de um menu multi-matéria
def split_summary_candidates(text: str | None) -> list[tuple[str, str]]:
    """Divide uma mensagem com vários sumários (um por matéria) em blocos.

    Cada bloco começa num cabeçalho de nível 1 (`= Título`). Útil quando um
    canal/grupo simples publica um único pin com vários sumários.
    """
    if not text:
        return []
    lines = text.splitlines()
    preface: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        heading = _heading_level(line)
        is_top = bool(heading and heading[0] == 1)
        if is_top:
            if current_lines:
                blocks.append((current_title or "Sumário", current_lines))
            current_title = heading[1]
            current_lines = [line]
        else:
            if current_lines:
                current_lines.append(line)
            else:
                preface.append(line)
    if current_lines:
        blocks.append((current_title or "Sumário", current_lines))

    if len(blocks) <= 1:
        return [(first_heading(text) or "Sumário", text)] if looks_like_menu(text) else []

    result: list[tuple[str, str]] = []
    for title, block_lines in blocks:
        block_text = "\n".join(block_lines).strip()
        tags = extract_hashtags(block_text)
        if len(tags) < 1 and heading_count(block_text) < 1:
            continue
        result.append((title, block_text))
    return result or [(first_heading(text) or "Sumário", text)]
