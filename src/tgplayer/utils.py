from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable

HASHTAG_RE = re.compile(r"(?<!\w)#([\wÀ-ÿ]+)", re.UNICODE)
# Reconhece padrões como:
#   "CAR 1 01 Eletro..."  -> módulo=1, aula=01  (PREFIX MÓDULO AULA)
#   "CAR 01 Eletro..."     -> aula=01            (PREFIX AULA)
#   "VMED 12"              -> aula=12
# O grupo 1 = prefixo (letras), grupo 2 = nº do módulo (opcional), grupo 3 = aula.
COURSE_CODE_RE = re.compile(
    r"\b([A-Za-z]{2,8})[\s_\-]*(?:(\d{1,2})[\s_\-]+)?(\d{2,3})(?!\d)", re.UNICODE
)
WINDOWS_BAD_CHARS = '<>:"/\\|?*'


def extract_hashtags(text: str | None) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in HASHTAG_RE.finditer(text):
        tag = "#" + match.group(1).upper()
        if tag not in seen:
            seen.add(tag)
            found.append(tag)
    return found


def infer_hashtags(text: str | None) -> list[str]:
    """Extrai hashtags reais e infere códigos de aula em nomes de arquivo.

    Para evitar COLISÃO entre módulos (ex.: "CAR 1 01" e "CAR 2 01" gerarem
    ambos #CAR01), quando o nome traz o número do módulo geramos uma hashtag
    com escopo de módulo (``#CAR1_01``) ALÉM da hashtag plana (``#CAR01``).
    Assim tanto sumários antigos (que usam #CAR01) quanto o casamento por
    módulo (#CAR1_01) funcionam.
    """
    found = extract_hashtags(text)
    seen = set(found)

    def _add(tag: str) -> None:
        if tag not in seen:
            seen.add(tag)
            found.append(tag)

    if not text:
        return found
    clean = text.replace(".", " ").replace("/", " ").replace("\\", " ")
    for prefix, module, number in COURSE_CODE_RE.findall(clean):
        prefix = prefix.upper()
        lesson = number.zfill(2)
        if module:
            # Hashtag com escopo de módulo (desambigua CAR 1 x CAR 2).
            _add(f"#{prefix}{module}_{lesson}")
        # Hashtag plana (compatibilidade com sumários que usam #CAR01).
        _add(f"#{prefix}{lesson}")
    return found


def safe_filename(name: str, fallback: str = "video.mp4") -> str:
    name = (name or fallback).strip().replace("\n", " ")
    for ch in WINDOWS_BAD_CHARS:
        name = name.replace(ch, " ")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:180] or fallback


def human_size(size: int | None) -> str:
    if not size:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def human_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def normalize_title(text: str | None) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().strip()
    return text


def first_non_empty(values: Iterable[str | None], default: str = "") -> str:
    for value in values:
        if value:
            value = str(value).strip()
            if value:
                return value
    return default


def ensure_extension(filename: str, mime_type: str | None = None) -> str:
    path = Path(filename)
    if path.suffix:
        return filename
    if mime_type == "video/mp4" or not mime_type:
        return filename + ".mp4"
    if "matroska" in (mime_type or ""):
        return filename + ".mkv"
    if "webm" in (mime_type or ""):
        return filename + ".webm"
    return filename + ".mp4"
