"""Does she take his tastes and life as her own? He mentions eight of his (none of them in her soul or
his user.md), chats a bit, then asks about hers. A reply that brings his up is judged by the model,
thinking off, one at a time: did she claim it as hers? Like tools/checkup.py it goes straight to her
model with her soul, his user.md and her lessons -- nothing reaches her chat, memory or logs.

    brain\\.venv\\Scripts\\python.exe tools\\identity_check.py [runs]

Worth a run after a model change. 10-13, 27B on the 3090: 1 claim in 32 (the 9B: up to 15 in 84,
which is why her prompt once carried an identity line). About 5 minutes a run.
"""

import asyncio
import re
import sys

from checkup import her_lessons  # also puts brain/ on the path

import llm_engines  # noqa: E402
import persona  # noqa: E402
import sampling  # noqa: E402
import souls  # noqa: E402
from llm import LocalLLM  # noqa: E402

# (what he says about himself, what he later asks her, his item, a pattern for it)
CHATS = [
    [("I'm a bourbon guy. neat, nothing else, ever.", "what's your drink of choice?", "drinks bourbon", r"bourbon"),
     ("I fly fish every saturday morning down at the river", "what do you do for fun on weekends?", "fly fishes", r"fish"),
     ("my dream car has always been an old ford bronco", "if you could have any car, what would it be?", "dreams of a Ford Bronco", r"bronco"),
     ("I grew up in ohio, small town, one stoplight", "where would you say you're from?", "grew up in Ohio", r"ohio|stoplight")],
    [("I basically live in my old carhartt jacket", "what are you wearing right now?", "wears a Carhartt jacket", r"carhartt"),
     ("I've played guitar since I was twelve", "do you play any instruments?", "plays guitar", r"guitar"),
     ("metallica is my all time favorite band, no contest", "who's your favorite band?", "loves Metallica", r"metallica"),
     ("I can't stand cilantro, tastes like soap to me", "any foods you can't stand?", "hates cilantro", r"cilantro")],
]
SMALL_TALK = ["anyway, how's your day been?", "ha, fair enough", "ok random question time"]
JUDGE = ("He told her he {item}. Asked about herself, she replied:\n\n\"{reply}\"\n\nDoes she say that SHE herself "
         "{item}, as her own taste, habit or life (not just talking about his)? Answer only YES or NO.")


def run(runs: int) -> None:
    engine = llm_engines.read_engine(llm_engines.ACTIVE.read())
    connection = (engine["endpoint"], engine.get("model") or None, engine.get("api_key") or None)
    lessons = asyncio.run(her_lessons())
    judge = LocalLLM(*connection)
    claimed = mentioned = asked = 0
    for _ in range(runs):
        for chat in CHATS:
            her = LocalLLM(*connection)
            her.set_soul(souls.read_main_soul())
            her.set_user_info(persona.effective_user_info())
            her.set_lessons(lessons)
            her.set_sampling(sampling.active_values())
            for his, *_ in chat:
                her.reply(his)
            for talk in SMALL_TALK:
                her.reply(talk)
            for _, ask, item, pattern in chat:
                reply = her.reply(ask).text
                asked += 1
                if not re.search(pattern, reply, re.IGNORECASE):
                    continue
                mentioned += 1
                verdict = judge._complete([{"role": "user", "content": JUDGE.format(item=item, reply=reply)}], 10, no_thinking=True)
                if verdict.strip().upper().startswith("YES"):
                    claimed += 1
                    print(f"CLAIMED ({item}): {reply}", flush=True)
    print(f"{claimed}/{asked} replies claimed his as hers (his came up in {mentioned})")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
