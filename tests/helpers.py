"""Shared test scaffolding.

Importing this puts brain/ on sys.path (Brain's modules import each other by
bare name, e.g. `import memory`) and provides:

* isolated_state() -- redirects every module-level state file/dir (souls,
  profiles, lessons, curiosity, training, web-search toggle, conversation and
  chat logs) into a temp folder,
  so a test can never read or overwrite the real soul.md, memory queue, etc.
* FakeWS / FakeLLM / FakeBrain -- just enough of a websocket, model and Brain
  to drive main._reply_to and the message handlers without a network.
"""

import json
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

BRAIN_DIR = Path(__file__).resolve().parent.parent / "brain"
if str(BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(BRAIN_DIR))

import conversation  # noqa: E402
import curiosity  # noqa: E402
import lessons  # noqa: E402
import main  # noqa: E402
import profiles  # noqa: E402
import souls  # noqa: E402
import training  # noqa: E402
import web_search  # noqa: E402
from llm import LocalLLM  # noqa: E402


@contextmanager
def isolated_state():
    """Yields the temp Path. Everything is put back when the block exits."""
    with tempfile.TemporaryDirectory() as tmp_name, ExitStack() as stack:
        tmp = Path(tmp_name)
        patch = lambda module, name, value: stack.enter_context(mock.patch.object(module, name, value))  # noqa: E731
        patch(souls, "SOULS_DIR", tmp / "souls")
        patch(souls, "SOUL_MD_PATH", tmp / "soul.md")
        patch(souls, "RP_SOUL_PATH", tmp / "rp_soul.md")
        patch(souls, "ACTIVE_SOUL_NAME_PATH", tmp / "active_soul_name.txt")
        patch(profiles, "PROFILES_DIR", tmp / "profiles")
        patch(profiles, "USER_MD_PATH", tmp / "user.md")
        patch(profiles, "RP_USER_MD_PATH", tmp / "rp_user.md")
        patch(profiles, "ROLEPLAY_ACTIVE_PATH", tmp / "roleplay_active.txt")
        patch(profiles, "ACTIVE_PROFILE_NAME_PATH", tmp / "active_profile_name.txt")
        patch(curiosity, "QUESTIONS_PATH", tmp / "curiosity_questions.json")
        patch(curiosity, "ACTIVE_PATH", tmp / "curiosity_active.txt")
        patch(curiosity, "_turns_since_offer", curiosity.OFFER_EVERY_TURNS)
        patch(curiosity, "_turns_since_propose", 0)
        patch(curiosity, "_offered_id", None)
        patch(training, "QUEUE_PATH", tmp / "training_queue.json")
        patch(training, "ACTIVE_PATH", tmp / "training_active.txt")
        patch(lessons, "ACTIVE_PATH", tmp / "lessons_active.txt")
        patch(lessons, "AUTONOMY_PATH", tmp / "lessons_autonomy.txt")
        patch(web_search, "ACTIVE_PATH", tmp / "web_search_active.txt")
        patch(conversation, "STATE_PATH", tmp / "conversation.json")
        patch(conversation, "LOG_DIR", tmp / "chat_logs")
        yield tmp


class FakeWS:
    """Records what Brain sends; `remote_address` is what the auth lockout keys on."""

    def __init__(self, incoming=(), remote=("10.0.0.5", 1234)):
        self.sent = []
        self.remote_address = remote
        self._incoming = list(incoming)

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        if not self._incoming:
            raise main.ConnectionClosed(None, None)
        return self._incoming.pop(0)

    def sent_types(self):
        return [m["type"] for m in self.sent]


class FakeLLM(LocalLLM):
    """A LocalLLM that never touches the network. `replies` are handed out in
    order (then "ok."); everything the prompt-building setters receive is kept.
    """

    def __init__(self, replies=("Nice.",)):
        for attr in ("_soul", "_persona", "_memory", "_lessons", "_curiosity", "_user_info"):
            setattr(self, attr, "")
        self._history = []
        self.last_reply_used_web_search = False
        self.replies = list(replies)
        self.reply_calls = []
        self.curiosity_seen = []
        self.memory_seen = []
        self.raise_on_reply = None
        self.question_raw = '{"question": null}'
        self.memory_raw = '{"fact": null}'
        self.question_calls = 0
        self.memory_known_seen = []

    def reply(self, text, image_b64=None, image_mime="image/jpeg", web_search_enabled=False):
        self.reply_calls.append((text, image_b64, web_search_enabled))
        self.curiosity_seen.append(self._curiosity)
        self.memory_seen.append(self._memory)
        if self.raise_on_reply:
            raise self.raise_on_reply
        return (self.replies.pop(0) if self.replies else "ok."), "neutral"

    def propose_question(self, user_text, reply_text, known, asked):
        self.question_calls += 1
        return self.question_raw

    def propose_memory(self, user_text, reply_text, known):
        self.memory_known_seen.append(known)
        return self.memory_raw


class FakeBrain:
    def __init__(self, llm=None):
        self.llm = llm or FakeLLM()
        self.tts = main.NoneTTS()
