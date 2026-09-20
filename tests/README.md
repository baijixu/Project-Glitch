# Tests

```
run-tests.bat            # Windows  (./run-tests.sh on Linux/macOS)
run-tests.bat tests.test_memory     # just one file
run-tests.bat -v                    # list every test
```

Stdlib `unittest` only, run with Brain's own virtualenv. No network, no Hindsight, no
LLM: a fake model and websocket drive the real Brain code, and every test is isolated to a
temp folder (`helpers.isolated_state`), so nothing here can touch your real `soul.md`,
`user.md`, memory, lessons or config.

| File | Covers |
| --- | --- |
| `test_isolation.py` | main `soul.md`/`user.md` are never written by role-play souls/profiles; which soul and user info are active |
| `test_memory.py` | retain mission (set, upgrade, never overwrite a hand-written one, restored after Clear Memory), core-fact recall, importance tags |
| `test_training.py` | memory-training review queue: propose, approve/edit/reject, failed saves, off = normal retain |
| `test_curiosity.py` | question pacing, never-ask list, repeats, role-play/toggle gating |
| `test_lessons_parse.py` | validating the model's lesson JSON, the bare-rating rule |
| `test_reply_path.py` | `_reply_to`: blank input, LLM failure, memory gating, search/image turns |
| `test_prompt.py` | order of the system prompt, identity line, time line |
| `test_auth.py` | token handshake and per-IP lockout |
| `test_web_search.py` | trust labels, ranking, failure handling |
| `test_backup.py` | `tools/backup_local_state.py` |

A test that passes is only useful if it can fail: after adding one, break the code it
covers on purpose and check it goes red.
