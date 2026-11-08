from __future__ import annotations

from zora.tts.manager import clean_for_speech, split_for_speech


def test_clean_for_speech_removes_markdown():
    text = """# Título

Aqui está **uma** resposta com `código` e [link](https://example.com).

```python
print("oi")
```

- item um
- item dois
"""
    clean = clean_for_speech(text)
    assert "```" not in clean
    assert "#" not in clean
    assert "print" not in clean
    assert "bloco de codigo omitido" in clean
    assert "link" in clean
    assert "**" not in clean


def test_clean_truncates_at_sentence():
    text = ("Frase curta. " * 50).strip()
    clean = clean_for_speech(text, max_chars=80)
    assert clean.endswith("...")
    assert len(clean) <= 84


def test_clean_for_speech_pronuncia_zora():
    assert clean_for_speech("A Zora chegou, Zora.") == "A Zóra chegou, Zóra."


def test_split_for_speech():
    text = ("Uma frase de teste. " * 40).strip()
    chunks = split_for_speech(text, max_chars=100)
    assert chunks
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert " ".join(chunks).startswith("Uma frase")
