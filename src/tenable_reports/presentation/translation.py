from __future__ import annotations

import re
from typing import Callable


TextTranslator = Callable[[str, str, str], str]


def split_translation_chunks(text: str, *, max_chars: int = 2500) -> tuple[str, ...]:
    """Divide texto por sentença e palavra sem descartar conteúdo."""

    normalized = " ".join(str(text or "").split())
    if not normalized:
        return ()
    sentences = re.split(r"(?<=[.!?;:])\s+", normalized)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        words = sentence.split()
        pieces: list[str] = []
        piece = ""
        for word in words:
            candidate = f"{piece} {word}".strip()
            if piece and len(candidate) > max_chars:
                pieces.append(piece)
                piece = word
            else:
                piece = candidate
        if piece:
            pieces.append(piece)
        for value in pieces:
            candidate = f"{current} {value}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = value
            else:
                current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks)


def translate_in_chunks(
    text: str,
    translator: TextTranslator,
    *,
    source_language: str = "en",
    target_language: str = "pt-BR",
    max_chars: int = 2500,
) -> tuple[str, ...]:
    """Traduz cada parte em ordem; plugin output nunca deve chamar esta função."""

    translated: list[str] = []
    for chunk in split_translation_chunks(text, max_chars=max_chars):
        value = str(translator(chunk, source_language, target_language)).strip()
        if not value:
            raise ValueError("O tradutor retornou um bloco vazio.")
        translated.append(value)
    return tuple(translated)
