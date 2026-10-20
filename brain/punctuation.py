"""Her punctuation, fixed after the model writes it: em-dashes (dedash). Applied once, where every
completion of hers comes back (llm.LocalLLM._complete) and to each sentence she says as it streams
(llm.Sentences) -- so the screen, her voice, her history and everything she writes agree. Machine-read
answers (YES / SAME / JSON) pass through it too; they have no dashes worth keeping."""

import re

# Josh doesn't want em-dashes from her (~150 a day in her replies, nearly all " — "). Rules over asking the
# model: a soul rule gets lost, and this covers every model. Each becomes what the sentence needs instead.
_DASH = r"(?:—| – )"
_TRAILING_DASH = re.compile(rf"\s*{_DASH}\s*(?=$|\n|[\"”’*)\]])")  # trailing off: "I was going to —"
_PAIRED_DASHES = re.compile(rf"\s*{_DASH}\s*([^—–.!?\n]{{1,80}}?)\s*{_DASH}\s*")  # an aside: "my dog — a husky — loves snow"
_ONE_DASH = re.compile(rf"([^\s—–]?)\s*{_DASH}\s*([\w']*)(?=([^.!?\n]*))")  # the rest of the sentence: looked at, kept
# A new clause after the dash ("it's not the money — it's respect") starts a new sentence; a list ("three things —
# rain, tea and you") or an explanation ("hands down — a dog gets you out of the chair") takes a colon; anything else
# ("— so", "— and", "— always tea") gets a comma.
_CLAUSE_START = set(
    "i i'm i'd i'll i've it it's that's this he he's she she's we we're they they're you're you've there's here's".split()
)
_LIST = re.compile(r",.*\b(?:and|or)\b")
_EXPLAINS = {"a", "an", "the"}
_JOINS = {"and", "but", "so", "or", "because", "though", "yet"}  # "— so, and more" is a comma, list or not


def dedash(text: str) -> str:
    """`text` with each em-dash (or spaced en-dash) as the punctuation the sentence needs instead."""
    if "—" not in text and " – " not in text:
        return text
    text = _TRAILING_DASH.sub("...", text)
    text = _PAIRED_DASHES.sub(r", \1, ", text)

    def one(match: re.Match) -> str:
        before, word = match[1], match[2]
        if not before or before in ",;:.!?…(":  # punctuation's already there (or nothing before it)
            return f"{before} {word}" if before else word
        if word.lower() in _CLAUSE_START:
            return f"{before}. {word[0].upper()}{word[1:]}"
        rest = word + match[3]
        if word.lower() not in _JOINS and (_LIST.search(rest) or (word.lower() in _EXPLAINS and len(rest.split()) >= 5)):
            return f"{before}: {word}"
        return f"{before}, {word}" if word else f"{before},"

    return re.sub(r",\s*([.!?,;:])", r"\1", _ONE_DASH.sub(one, text))
