# etsy-pod-agent

Claude Code skills and a Python pipeline (`podagent`) that turn print-on-demand wall-art designs into checked Etsy **draft** listings. A person approves concepts before any spend (Gate 1) and clicks Publish in Etsy (Gate 2). The README explains the whole flow.

## Skills
- `/setup-shop` (`.claude/skills/setup-shop/`): one-time setup. Creates the config, connects Etsy and the print provider, sets up rooms.
- `/create-listings` (`.claude/skills/create-listings/`): runs a batch in inbox or generate mode.

For listing work, follow the skill. This file covers everything else: questions, debugging and changes to the code.

## How the work is split
- **Python does everything with a right answer:** Etsy limits, prices, image measurements, state, API calls and budgets.
- **Claude does the judgement:** reading images for spelling and IP, writing concepts and copy.
- Claude's output is JSON that code validates. Never move a rule from code into a prompt. If a rule can be checked, check it in code.

## Rules for every session
- **Never publish.** Never send a listing `state`, and never click Publish in a browser. The Etsy client allow-lists its requests; don't widen that list to allow publishing.
- **Never ask for a key, secret or token in chat.** Never print `.env` or `.secrets/`. The user edits `.env` themselves.
- **Ask before spending money.** Image generation and room generation cost money. `.claude/settings.json` makes Claude Code ask before `podagent decide`, `generate`, `rooms-generate` and `rooms-regenerate`. Keep those rules.
- **Holds stand.** A design that fails a check stays held, with its reason. Report it; don't talk it away.
- **Never edit a batch's `state.json` by hand.** Use `podagent status`, `mark` and `release`.

## Layout
- `src/podagent/cli.py`: every command (`podagent --help`).
- `pipeline.py` and `state.py`: batch steps and resumable state. Each step skips finished work, so `podagent run <batch>` is safe to repeat.
- `etsy/`, `providers/`, `generate.py`, `http.py`: external APIs. HTTP goes through `http.py`, which never retries a create on a 5xx.
- `checks.py`, `rules.py`, `listing_copy.py`, `pricing.py`, `costs.py`: the checks and limits.
- `images/`: crops, upscaling, mockups and room calibration.
- Working data (`config.toml`, `.env`, `designs/`, `batches/`, `rooms/`) is gitignored. Never commit it.

## Before calling a change done
```
ruff format .
ruff check .
pytest
podagent demo
python scripts/scan_secrets.py --all
```
- Tests never touch the network. Fake APIs with `http.Recorder` and assert the exact request payload.
- Every behaviour change gets a test. Bug fixes get a test that fails without the fix.
- CI runs the same checks on Ubuntu and Windows, Python 3.11 and 3.13.
- Enable the pre-commit secret scan once: `git config core.hooksPath .githooks`.

## Docs to keep true
If you change a command, flag or behaviour, update the README and both `SKILL.md` files in the same change. Every command and flag they mention must exist in `cli.py`.
