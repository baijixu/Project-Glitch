"""Matching words without a dictionary: the small words that carry no topic (STOPWORDS), and just enough
normalizing (norm) and stemming (stem) to match "climbing"/"climb". Curiosity compares her questions with
these, training spots a fact already waiting, and her gallery finds a picture by what it shows."""

import re

STOPWORDS = frozenset(
    "the a an and or of to in on at for with your you you're youre is are was were be do does did it its this that "
    "what whats how when where which who why about into from more been have has had will would could can just like "
    "one first most really still "
    # filler verbs and words that carry no topic ("how did you get into X?" = "what got you started on X?")
    "get got gotten getting start started make made take took going ever yet any some there here them they "
    "their now way place thing things kind sort much many lot also even".split()
)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def stem(word: str) -> str:
    """Just enough to match "climbing"/"climb" and "names"/"name" -- no dictionary."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word
