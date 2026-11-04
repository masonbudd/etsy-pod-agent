---
name: create-listings
description: Turn print-on-demand wall-art designs into checked Etsy draft listings with room mockups, listing copy, prices, print files and a review page. Generate mode researches what sells, proposes concepts for approval and generates the art; inbox mode starts from the user's own designs in designs/inbox/. Never publishes. Use when the user runs /create-listings [count], or says "list my new designs", "make some new prints" or similar.
argument-hint: "[count] [inbox|generate]"
---

# Create listings

**Request:** $ARGUMENTS. Count: the number given, else `batch.default_count` in `config.toml`, never more than `batch.max_count` (20).
**Mode:** if the user said "inbox" or "my own designs", or there are images in `designs/inbox/` and they didn't ask for new art, use inbox mode. If they asked for new designs, use generate mode. If unclear, ask once.

## Rules that never bend
1. **Never publish.** This skill makes Etsy drafts and a review page. The user clicks Publish in Etsy. Never click Publish in a browser and never send a listing `state`.
2. **Gate 1.** Nothing is generated until the user approves, edits or rejects each concept. Record only decisions the user actually gave you in this conversation.
3. **A design that fails a check is held**, with its reason, inside its batch. Report holds; never talk a hold away.
4. **Originality.** Use research for themes and buyer language only. Never copy or closely imitate a specific listing's title, wording or design, and never name a living artist in a prompt.
5. **Browser steps:** stop and ask before any click that can't be undone, and before any step a playbook marks `[ASK]`.

## Generate mode: research, concepts, Gate 1, images

**G1. Start:** `podagent new --generate --count N`. Note the batch ID.

**G2. Research.**
- If ProfitTree MCP tools are available, use its keyword research for the user's niche. Save phrases and numbers only to `batches/<batch>/research/profittree.json` as `{"keywords": [{"phrase": "...", "searches": 0, "competition": 0, "sales": 0}]}`. Never save listing titles, shop names, URLs or images.
- Then run `podagent research <batch>`. It merges ProfitTree with `research/keywords.csv`, drops banned terms and ranks the phrases into `research/research.json`.

**G3. Concepts.** Read `research/research.json` and write `batches/<batch>/concepts.json`:
```json
{"concepts": [{"id": "sun-over-hills", "title": "Sun over hills", "target_keyword": "minimalist landscape print",
  "why": "one or two sentences from the research", "prompt": "the image prompt",
  "lettering": "only if the design has words: the exact text", "research_phrases": ["..."]}]}
```
Aim for N concepts that are clearly different from each other. Write prompts for simple, printable art: a clear subject, a few flat colours, portrait. Don't add border instructions; the config adds them.

**G4. Gate 1.** Run `podagent concepts <batch>`.
- It checks the concepts, adds each one's cost estimate (with the worst case of one retry) and writes `concepts.html`. Fix any errors it lists and rerun.
- Show the user the table it prints and the page path. Ask them to approve, edit or reject each concept. **Stop here and wait.**
- Record exactly what they said: `podagent decide <batch> --approve id1 id2 --reject id3 --edit id4 "new prompt" --note "their reason"`.
- If you change a prompt yourself after approval, the approval is cleared and you must ask again.

**G5. Generate:** `podagent generate <batch>`. It generates approved concepts only and stops at `images.max_batch_usd`. If it reports a request that was paid for but not saved, tell the user before using `--retry-unsaved`.

Then continue with the shared steps below.

## Inbox mode
**I1.** Run `podagent new --count N`. Designs move from `designs/inbox/` into the batch.

## Shared steps

**1. Run the pipeline:** `podagent run <batch>` (add `--dry-run` for a first try). It exits with code 2 and lists what it is waiting for. Repeat it after each step below; it skips finished work.

**2. Visual review** (waiting for "vision review"). Read each listed `preview.jpg` and write `vision.json` next to it:
```json
{"lettering": "", "spelling_ok": true, "spelling_notes": "", "ip_ok": true, "ip_notes": "",
 "border_seen": false, "defects_ok": true, "defects_notes": "", "subject": ""}
```
- `lettering`: every word in the image, exactly as drawn, letter by letter. Empty if none.
- `spelling_ok`: false for any misspelt, garbled or extra letter.
- `ip_ok`: false for brands, logos, characters, celebrities, sports teams, or a recognisable copy of a living artist's work.
- `border_seen`: true for any margin, painted border or paper edge around the art.
- `defects_ok`: false for garbled shapes, extra limbs, watermarks or obvious artefacts.
- `subject`: one plain line describing the design, used in alt text. No brand or artist names.
Be strict: when unsure, mark it false and explain in the notes. For generated designs, code also compares the lettering with the concept's.

**3. Holds.** After the rerun, list each held design with its reason and suggested fix.
- Generated designs: run `podagent generate <batch>` once more. It regenerates each held design one time, with the fix added to the prompt (this was in the Gate 1 worst-case estimate). The new image goes back to step 2. If it is held again, it stays held.
- Inbox designs with a border found by code: offer `podagent trim-border <batch> <item>` once.
- Anything else stays held for the user. `podagent release <batch> <item>` puts it back after they fix it.

**4. Listing copy** (waiting for "listing copy").
- Run `podagent brief <batch>` for the voice, title style, tag rules, sections, banned terms and the items.
- Write `batches/<batch>/items/<item>/copy.json` for each item: `title`, `hook` (2–4 sentences that open the description), exactly 13 `tags`, `section`. Use the batch's research phrases where they fit.
- The rest of the description (sizes, frames, delivery, AI disclosure) is added by code. Don't repeat it in the hook.
- For more than 10 items, split the copy across subagents, each with the brief.
- If the run reports "fix listing copy", read `copy_errors.json`, fix the copy and rerun. After three failed versions the item is held.

**5. Images.** Upscaling with Real-ESRGAN takes a few minutes per design. For more than two designs, run `podagent run <batch>` in the background and check its output when it finishes.

**6. Drafts.** Without `--dry-run`, the run creates each Etsy draft with its photos and every size and frame. A rerun never creates a second draft.

**7. AI disclosure.** Etsy's API has no field for it, so it is a browser step with Claude in Chrome.
- Work through `batches/<batch>/disclosure_tasks.md`. For each draft: open the edit link, tick the option saying the design was made with an AI generator, then click **Save as draft**.
- If there is no Save as draft button or the page looks different, stop and ask.
- After each draft, run `podagent mark <batch> <item> disclosure`.
- Ask the user to keep the Chrome window visible; hidden tabs pause.

**8. Print provider.** The run's output says what is left:
- `local_export`: tell the user where `provider_export/` and `sku_sheet.csv` are.
- `browser`: follow the playbook with Claude in Chrome using `provider_export/browser_tasks.json`. Use one `file_upload` call per entry in `upload_calls`, which keeps each call under 10 MB. Stop and ask at every `[ASK]` step.
- `prodigi`: print files are uploaded already. After the user publishes, run `podagent provider-link <batch>`. Prodigi only sees listings once they are live.

**9. Hand over.**
- Run `podagent review <batch>`.
- Give the user the path to `batches/<batch>/review.html`, plus a few lines from `report.md`: concepts approved, drafts made, holds with reasons, prices below the minimum margin, time taken, image cost.
- End with: check each draft in Etsy, then click Publish yourself.
