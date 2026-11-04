# Recorded agent outputs

Demo mode replays these instead of asking Claude, so it runs with no accounts.
Each folder holds what Claude wrote for one sample design:

- `vision.json`: the visual review (lettering, spelling, IP, border, defects, subject).
- `copy.json`: the listing title, opening paragraph, 13 tags and shop section.

The code steps still validate both files exactly as they would in a live run.

`generate/` holds the generate-mode demo's inputs:

- `concepts.json`: the concepts Claude proposed from the sample research.
- `decisions.json`: sample Gate 1 decisions (approve, edit, reject), standing in for the ones a user gives in chat.
