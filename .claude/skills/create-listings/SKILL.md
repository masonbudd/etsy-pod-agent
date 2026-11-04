---
name: create-listings
description: Turn the designs in designs/inbox/ into checked Etsy draft listings with room mockups, listing copy, prices, print files and a review page. Never publishes. Use when the user runs /create-listings [count], or says "list my new designs" or similar.
argument-hint: "[count]"
---

# Create listings

**Requested count:** $ARGUMENTS. If no number was given, use `batch.default_count` from `config.toml`. Never more than `batch.max_count` (20).

## Rules that never bend
1. **Never publish.** This skill makes Etsy drafts and a review page. The user clicks Publish in Etsy. Never click Publish in a browser and never send a listing `state`.
2. **A design that fails a check is held**, with its reason, inside its batch. Report holds; do not talk a hold away.
3. **Originality.** Use research for themes and buyer language only. Never copy or closely imitate a specific listing's title, wording or design.
4. **Browser steps:** stop and ask the user before any click that can't be undone, and before any step a playbook marks `[ASK]`.

## Steps

**1. Check and start.**
- Run `podagent doctor`. If Etsy or the provider fails, tell the user. Offer a dry run, which needs no accounts.
- Run `podagent new --count N`. Note the batch ID.

**2. Run the pipeline:** `podagent run <batch>` (add `--dry-run` for a first try).
It advances every design as far as it can. It exits with code 2 and lists what it is waiting for. Repeat this command after each step below; it skips finished work.

**3. Visual review** (waiting for "vision review"). Read each listed `preview.jpg` and write `vision.json` next to it:
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
Be strict. When unsure, mark it false and explain in the notes. A hold costs a minute; a takedown costs the shop.

**4. Holds.** After the rerun, list each held design with its reason and suggested fix.
- For a border found by code, offer `podagent trim-border <batch> <item>` once. The trimmed design is reviewed again. If it fails again, leave it held.
- Anything else stays held for the user to decide. `podagent release <batch> <item>` puts it back after they fix it.

**5. Listing copy** (waiting for "listing copy").
- Run `podagent brief <batch>` for the voice, title style, tag rules, sections, banned terms and the items.
- Write `batches/<batch>/items/<item>/copy.json` for each item: `title`, `hook` (2–4 sentences that open the description), exactly 13 `tags`, `section`.
- The rest of the description (sizes, frames, delivery, AI disclosure) is added by code. Don't repeat it in the hook.
- For more than 10 items, split the copy across subagents, each with the brief.
- If the run reports "fix listing copy", read `copy_errors.json`, fix the copy and rerun. After three failed versions the item is held.

**6. Images.** Upscaling with Real-ESRGAN takes a few minutes per design. For more than two designs, run `podagent run <batch>` in the background and check its output when it finishes.

**7. Drafts.** Without `--dry-run`, the run creates each Etsy draft with its photos and every size and frame. A rerun never creates a second draft.

**8. AI disclosure.** Etsy's API has no field for it, so it is a browser step with Claude in Chrome.
- Work through `batches/<batch>/disclosure_tasks.md`. For each draft: open the edit link, tick the option saying the design was made with an AI generator, then click **Save as draft**.
- If there is no Save as draft button or the page looks different, stop and ask.
- After each draft, run `podagent mark <batch> <item> disclosure`.
- Ask the user to keep the Chrome window visible; hidden tabs pause.

**9. Print provider.** The run's output says what is left:
- `local_export`: tell the user where `provider_export/` and `sku_sheet.csv` are.
- `browser`: follow the playbook with Claude in Chrome using `provider_export/browser_tasks.json`. Use one `file_upload` call per entry in `upload_calls`, which keeps each call under 10 MB. Stop and ask at every `[ASK]` step.
- `prodigi`: print files are uploaded already. After the user publishes, run `podagent provider-link <batch>`. Prodigi only sees listings once they are live.

**10. Hand over.**
- Run `podagent review <batch>`.
- Give the user the path to `batches/<batch>/review.html`, plus a few lines from `report.md`: drafts made, holds with reasons, prices below the minimum margin, time taken, API cost.
- End with: check each draft in Etsy, then click Publish yourself.
