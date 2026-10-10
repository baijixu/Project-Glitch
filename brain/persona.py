"""Who she is right now: which soul and user info apply, and which saved
conversation -- all depending on whether role-play is on.
"""

import re

import conversation
import profiles
import souls
from hub import Brain

def effective_soul() -> str:
    """Her soul right now: the role-play soul during role-play (if one is selected),
    otherwise her main soul -- "" means LocalLLM's built-in default."""
    if profiles.read_roleplay_active():
        rp_soul = souls.read_active_soul()
        if rp_soul.strip():
            return rp_soul
    return souls.read_main_soul()


def user_name(info: str | None = None) -> str:
    """Their name from their profile ("Name: Josh", "my name is Josh"), or "". `info` is
    the profile to read; the effective one by default (none during role-play)."""
    found = re.search(r"name\s*(?::|is)\s*([^\n.,]+)", effective_user_info() if info is None else info, re.IGNORECASE)
    return found.group(1).strip() if found else ""


# Cap on how much of the user's own user.md goes into one prompt -- it's
# their own writing, but an enormous file would eat context on every turn.
MAX_USER_INFO_CHARS = 4000


def effective_user_info() -> str:
    """His user.md, read fresh each turn -- or "" during role-play, when the role-play
    profile stands in and his real details pause like memory does."""
    if profiles.read_roleplay_active():
        return ""
    return profiles.read_main_user().strip()[:MAX_USER_INFO_CHARS]


def conversation_mode() -> str:
    return conversation.ROLEPLAY if profiles.read_roleplay_active() else conversation.MAIN


def apply_conversation_mode(brain: Brain, persona: str) -> None:
    """Brings the LLM in line with the current mode after role-play is toggled:
    the right soul and persona, and the right saved conversation -- without the
    wipe set_soul/set_persona do (those are for switching to a different
    character, where a fresh conversation is the point).
    """
    brain.llm.update_soul(effective_soul())
    brain.llm.update_persona(persona)
    brain.llm.restore_history(conversation.load_state(conversation_mode()))
