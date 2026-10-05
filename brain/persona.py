"""Who she is right now: which soul and user info apply, and which saved
conversation -- all depending on whether role-play is on.
"""

import re

import conversation
import profiles
import souls
from hub import Brain

def effective_soul() -> str:
    """Which soul she should be running right now. During role-play it's the
    selected role-play soul (rp_soul.md), if one is selected; otherwise --
    role-play off, or on with no RP soul picked -- it's her main soul
    (soul.md), and "" from that means LocalLLM's built-in default. The two
    files are never merged or copied into each other (see souls.py), so a
    role-play soul can't leak into or overwrite her permanent personality.
    """
    if profiles.read_roleplay_active():
        rp_soul = souls.read_active_soul()
        if rp_soul.strip():
            return rp_soul
    return souls.read_main_soul()


def user_name() -> str:
    """Their name from their profile ("Name: Josh", "my name is Josh"), or ""."""
    found = re.search(r"name\s*(?::|is)\s*([^\n.,]+)", effective_user_info(), re.IGNORECASE)
    return found.group(1).strip() if found else ""


# Cap on how much of the user's own user.md goes into one prompt -- it's
# their own writing, but an enormous file would eat context on every turn.
MAX_USER_INFO_CHARS = 4000


def effective_user_info() -> str:
    """What the user wrote about themselves (their main user.md), or "" during
    role-play -- the selected role-play profile (rp_user.md) stands in for
    "who the user is" then, and their real details pause the same way memory
    and lessons do. Read from disk fresh each turn, so an edit made in a text
    editor applies to her very next reply with no restart.
    """
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
