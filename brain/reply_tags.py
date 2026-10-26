"""The tags she writes into a reply for the app, never for him -- [mood: x] (her face and voice), [read: x] (how
she read him, protocol.her_read) and [secret: x] (a game's pick) -- and her reply cut into sentences as it streams
(Sentences, for reply.py), with every tag taken out. One table (_TAGS) says how each is read; take_tags reads a
finished reply, untag cleans a piece of one."""

import re
from typing import NamedTuple

from punctuation import dedash

# The avatar's expression presets; "neutral" means none of them.
VALID_MOODS = {"happy", "angry", "sad", "relaxed", "surprised", "neutral"}
# Moods she writes that aren't presets, as the nearest one that is (in 72 test replies: curious
# 12, concerned 6, thoughtful 1). Curious isn't "surprised": that face is too wide-eyed to use
# on a sixth of her replies.
MOOD_ALIASES = {
    "curious": "happy", "excited": "happy", "playful": "happy", "amused": "happy", "flirty": "happy",
    "thoughtful": "relaxed", "calm": "relaxed", "content": "relaxed", "tired": "relaxed", "nostalgic": "relaxed",
    "concerned": "sad", "worried": "sad", "sympathetic": "sad",
    "annoyed": "angry", "frustrated": "angry",
    "shocked": "surprised", "amazed": "surprised",
}
# How she may read his last message (llm.READ_TAG_INSTRUCTION).
READS = ("serious", "playful", "goofing", "teasing", "sentimental", "venting", "ornery", "flirty", "curious", "confused",
         "tired", "down", "upset")


def _mood(word: str) -> str:
    """A mood tag's word as she wrote it, if her face or voice knows it ("neutral" if not): her
    voice speaks each one its own way (voice/tts.py's MOOD_STYLES); face() picks her expression."""
    word = word.lower()
    return word if word in VALID_MOODS or word in MOOD_ALIASES else "neutral"


def face(mood: str) -> str:
    """Her expression for a mood: the preset itself, or the nearest one (MOOD_ALIASES)."""
    return mood if mood in VALID_MOODS else MOOD_ALIASES.get(mood, "neutral")


# Also bare on its own line ("mood: relaxed", no brackets): the 9B writes it that way now and then.
# Spaces inside the brackets too ("[ Mood: relaxed ]"): the 27B's way, and her voice read those aloud.
# And no "mood:" at all ("[amused]"), only for a word her face or voice knows: 22 of her replies
# opened that way, her face stayed neutral and her voice read the tag out.
_MOOD_WORDS = "|".join(sorted(VALID_MOODS | set(MOOD_ALIASES)))
_TAG_OPEN = rf"(?:\[[ \t]*mood:[ \t]*|\[[ \t]*(?=(?:{_MOOD_WORDS})[ \t]*\]))"
_MOOD_TAG = re.compile(rf"(?:{_TAG_OPEN}|^[ \t]*mood:[ \t]*)(\w+)(?:[ \t]*\]|[ \t]*$)", re.IGNORECASE | re.MULTILINE)
_READ_TAG = re.compile(r"\[[ \t]*read:[ \t]*(\w+)[ \t]*\]", re.IGNORECASE)
# Something she keeps from him for a game: "[secret: a lighthouse]" anywhere in her reply, never shown or
# spoken, kept on her message in history and handed back in her turn notes (SECRET_NOTE) -- her thinking
# is dropped after each reply, so a pick made there is gone by his next question. "[secret: none]" ends it.
_SECRET_TAG = re.compile(r"[ \t]*\[[ \t]*secret[ \t]*:[ \t]*([^\]\n]*?)[ \t]*(?:\]|(?=\n)|$)", re.IGNORECASE)
_SECRET_OPEN = re.compile(r"\[[ \t]*secret[ \t]*:[^\]\n]*$", re.IGNORECASE)  # still arriving: wait before speaking
NOTES_BLOCK = re.compile(r"(?:\[notes\]|<notes>).*?(?:\[/notes\]|</notes>)", re.DOTALL | re.IGNORECASE)
_NOTES_OPEN = re.compile(r"\[notes\]|<notes>", re.IGNORECASE)


class Tags(NamedTuple):
    mood: str  # "neutral" with no tag, or a word her face and voice don't know
    read: str  # one of READS, "" if she gave none
    secret: str | None  # None with no tag, "" once she ends it ([secret: none])


def _secret(found: list[str]) -> str:
    secret = found[-1].strip()
    return "" if secret.lower() in ("", "none") else secret


# Each tag: its field, its pattern, its value from what was found (in order), and its value when there's none.
# Taken in this order, the secret first: what's left between tags is her reply.
_TAGS = (
    ("secret", _SECRET_TAG, _secret, None),
    ("read", _READ_TAG, lambda found: next((w.lower() for w in found if w.lower() in READS), ""), ""),
    ("mood", _MOOD_TAG, lambda found: _mood(found[-1]), "neutral"),  # a tag mid-reply goes too; the last one wins
)


def take_tags(text: str) -> tuple[Tags, str]:
    """Her finished reply's tags, and the reply without them."""
    values = {}
    for field, pattern, value, missing in _TAGS:
        found = pattern.findall(text)
        values[field] = value(found) if found else missing
        text = pattern.sub("", text)
    return Tags(**values), text.strip()


def untag(text: str) -> str:
    """A piece of her reply without any of her tags."""
    for _, pattern, _, _ in _TAGS:
        text = pattern.sub("", text)
    return text


# Her reply as it streams, cut into sentences to speak (reply.py). Her mood tag opens the
# reply (llm.MOOD_TAG_INSTRUCTION), so nothing is said until it's there: what comes before it
# is thinking the model leaked into its answer, or a tool call it wrote out as text.
LEADING_MOOD_TAG = re.compile(rf"\s*(?:{_TAG_OPEN}|mood:[ \t]*)(\w+)(?:[ \t]*\]|[ \t]*\n)", re.IGNORECASE)  # a bare one once its line ends
_NOT_HER_REPLY = re.compile(r".*(?:</think>|</function>|</tool_call>)", re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")  # ponytail: "Dr. Smith" splits too -- harmless when spoken


class Sentences:
    """Feed it the reply as it streams; get back each sentence once it's complete.
    mood stays None until her leading [mood: x] tag has arrived."""

    def __init__(self) -> None:
        self.mood: str | None = None
        self._text = ""

    def feed(self, piece: str) -> list[str]:
        self._text += piece
        if _SECRET_OPEN.search(self._text):
            return []
        self._text = _SECRET_TAG.sub("", self._text)  # anywhere, even before her mood tag
        if self.mood is None:
            self._text = _NOT_HER_REPLY.sub("", self._text)
            tag = LEADING_MOOD_TAG.match(self._text)
            if not tag:
                return []
            self.mood = _mood(tag[1])
            self._text = self._text[tag.end():]
        # Planning she wrote into a copied notes block isn't her reply: wait for it to close, then drop it.
        self._text = NOTES_BLOCK.sub("", self._text)
        if _NOTES_OPEN.search(self._text):
            return []
        # Split first: a bare tag still arriving ("mood: rel") must not be taken for a whole one.
        *done, self._text = _SENTENCE_END.split(self._text)
        return [dedash(s) for sentence in done if (s := untag(sentence).strip())]

    def finish(self, reply_text: str) -> list[str]:
        """What's left to say once the reply is complete. One that never opened with its
        mood tag wasn't spoken while streaming: all of it is, from the finished text."""
        rest = reply_text if self.mood is None else untag(self._text)
        return [dedash(sentence.strip()) for sentence in _SENTENCE_END.split(rest) if sentence.strip()]
