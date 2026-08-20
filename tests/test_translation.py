from tenable_reports.presentation.translation import (
    split_translation_chunks,
    translate_in_chunks,
)


def test_translation_chunks_preserve_all_text_in_order() -> None:
    source = " ".join(f"Sentence {index} has technical content." for index in range(100))
    chunks = split_translation_chunks(source, max_chars=180)
    assert len(chunks) > 1
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert " ".join(chunks) == source


def test_each_chunk_is_translated_separately() -> None:
    calls = []

    def translator(text, source, target):
        calls.append((text, source, target))
        return f"TRADUZIDO:{text}"

    result = translate_in_chunks(
        "First sentence. Second sentence. Third sentence.",
        translator,
        max_chars=20,
    )
    assert len(calls) >= 3
    assert len(result) == len(calls)
    assert all(item.startswith("TRADUZIDO:") for item in result)
