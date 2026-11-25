"""Match recent headlines while keeping the original reports available."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

HAN_CHARACTER = re.compile(r"[\u4e00-\u9fff]")
CLAUSE_BOUNDARY = re.compile(r"[，。！？；;]|,(?!\d)")
NUMERIC_VALUE = re.compile(r"[+\-−]?\d+(?:[.,]\d+)*%?")
WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class Headline:
    normalized: str
    numbers: tuple[str, ...]
    words: frozenset[str]
    lead_prefix: str
    lead_trigrams: frozenset[str]


@lru_cache(maxsize=4096)
def headline(text: str) -> Headline:
    normalized = unicodedata.normalize("NFKC", html.unescape(text)).casefold().replace("’", "'")
    tokens = WORD.findall(normalized.replace("'", ""))
    lead_prefix = ""
    lead_trigrams: frozenset[str] = frozenset()
    if HAN_CHARACTER.search(normalized):
        lead = "".join(WORD.findall(CLAUSE_BOUNDARY.split(normalized, maxsplit=1)[0]))
        if HAN_CHARACTER.search(lead[:4]):
            lead_prefix = lead[:4]
        lead_trigrams = frozenset(lead[i : i + 3] for i in range(len(lead) - 2))
    return Headline(
        " ".join(tokens),
        tuple(NUMERIC_VALUE.findall(normalized)),
        frozenset(tokens),
        lead_prefix,
        lead_trigrams,
    )


def same_story(
    original: Headline,
    canonical: Headline,
    other_original: Headline,
    other_canonical: Headline,
    *,
    same_category: bool,
) -> bool:
    if original.numbers != other_original.numbers:
        return False
    if original.normalized and original.normalized == other_original.normalized:
        return same_category or len(original.normalized) >= 20
    # A shortened copy can match even when translations are unavailable.
    if min(len(original.words), len(other_original.words)) >= 7 and (
        original.normalized.startswith(other_original.normalized + " ")
        or other_original.normalized.startswith(original.normalized + " ")
    ):
        return True

    if canonical.numbers != other_canonical.numbers:
        return False
    if (
        same_category
        and canonical.normalized
        and canonical.normalized == other_canonical.normalized
    ):
        return True

    # Compare the translated lead clause, where the core event is usually stated.
    # The original wording must corroborate the match when both originals are non-Chinese.
    if not canonical.lead_prefix or canonical.lead_prefix != other_canonical.lead_prefix:
        return False
    shared_trigrams = len(canonical.lead_trigrams & other_canonical.lead_trigrams)
    trigram_total = len(canonical.lead_trigrams) + len(other_canonical.lead_trigrams)
    if shared_trigrams < 8 or 2 * shared_trigrams < 0.66 * trigram_total:
        return False
    if original.lead_trigrams or other_original.lead_trigrams:
        return same_category and 2 * shared_trigrams >= 0.88 * trigram_total
    shared_words = len(original.words & other_original.words)
    if 2 * shared_trigrams >= 0.80 * trigram_total:
        return shared_words >= 5
    return (
        same_category
        and shared_words >= 5
        and shared_words >= 0.45 * min(len(original.words), len(other_original.words))
    )
