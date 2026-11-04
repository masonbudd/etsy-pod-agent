---
name: setup-shop
description: One-time setup for etsy-pod-agent. Creates config.toml, connects Etsy and the print provider, checks every tool, and calibrates the room templates for mockups. Use when the user runs /setup-shop, says "set up my shop", or opens this repo for the first time.
---

# Set up the shop

Goal: a valid `config.toml` and every connection checked, so `/create-listings` can run.

## Rules
- Never ask the user to paste a key, secret or token into the chat. They type them into `.env` themselves.
- Never print the contents of `.env` or `.secrets/`.
- Ask before downloading or installing anything.
- Run `podagent doctor` after each change and fix problems in the order it lists them.

## Steps

**1. Install and create the files.**
```
pip install -e .
podagent init
```
This creates `config.toml`, `.env`, `designs/inbox/`, `batches/` and `rooms/` (a copy of the sample rooms).

**2. Fill in `config.toml` with the user.** Ask these together, offering the example values as defaults:
- shop currency and spelling (British or American);
- the sizes they sell, and what their print provider charges for each, delivered (from the provider's price list);
- frame options, or none;
- target margin and minimum margin; check the Etsy fee rates match their country;
- brand voice, title style, shop sections, words to avoid;
- extra banned or IP terms for their niche;
- SKU prefix and default batch size (the cap is 20).
Edit the file, then run `podagent doctor`. Config errors appear first.

**3. Connect Etsy.**
1. The user creates an app at https://www.etsy.com/developers/your-apps and adds the callback URL from `ETSY_REDIRECT_URI` in `.env` (default `http://localhost:3456/callback`).
2. They put the keystring and shared secret in `.env`.
3. Run `podagent etsy-login`. A browser opens; the user approves access.
4. Run `podagent etsy-discover`. It fills in the shop, shipping profile, return policy, processing profile and production partner. If there are several of any, show the list and ask which one to use, then set it in `config.toml`.

**4. Choose the print provider adapter** (`provider.adapter`):
- `prodigi`: API adapter. See `docs/providers.md`. The user connects their Etsy shop in Prodigi's dashboard, sets `PRINT_PROVIDER_API_KEY` in `.env` (a `sandbox_` key first), sets `provider.prodigi.sales_channel_id`, and fills `provider.prodigi.catalogue` with a Prodigi SKU for every size.
- `local_export`: no account needed. Each batch gets print files grouped by ratio plus a SKU sheet to upload by hand.
- `browser`: for providers with no API. The user writes a playbook for their provider's site (`docs/browser-adapter.md`) and sets `provider.browser_playbook`.

**5. Real-ESRGAN (optional, recommended).** Without it, designs are enlarged with a plain resize, which is softer at large sizes. If the user wants it, point them to the `realesrgan-ncnn-vulkan` release for their system at https://github.com/xinntao/Real-ESRGAN/releases and ask before downloading. Set `REALESRGAN_PATH` in `.env`.

**6. Claude in Chrome.** Needed for the AI-disclosure tick on each draft and for the browser adapter. Check the extension's tools are available. If not, tell the user how to install the Claude in Chrome extension.

**7. Rooms for mockups.** The sample rooms work as they are. The user can replace them two ways.

*Generate rooms* (needs `OPENAI_API_KEY`):
1. Agree the scenes, frame colours and ratios with the user and set `[rooms]` in `config.toml`.
2. Run `podagent rooms-generate --count 3`. It prints the cost and spends nothing. Show the user and ask.
3. On a yes, run it again with `--confirm`. Each room is generated with an empty grey frame of a known ratio, then calibrated. Results go to `rooms/candidates/`.
4. Show the user each candidate image and its warnings. For each one they choose:
   - `podagent rooms-approve <name> --alt "in a black frame above a linen sofa"` adds it to `rooms/rooms.toml`;
   - `podagent rooms-regenerate <name> --confirm` makes a new one (costs one image);
   - `podagent rooms-reject <name>` drops it.
   A candidate with no frame found can't be approved.

*Use your own room photos:*
1. Add room images, each with one empty frame filled with plain light grey, to `rooms/`.
2. List them in `rooms/rooms.toml` with the frame's ratio (`"4:5"`, `"3:4"`, `"2:3"` or `"ISO"`) and a short alt-text phrase.
3. Run `podagent rooms-calibrate`, open `rooms/calibration_check.jpg` and ask the user to approve each room.

In both cases the first room in `rooms.toml` becomes each listing's main photo, so its frame must fit Etsy's square search thumbnail. Remove the sample rooms from `rooms.toml` when the user's own are in.

**8. Research source (for generate mode).** Check whether ProfitTree MCP tools are available. If not, the user can keep a `research/keywords.csv` with columns `keyword, searches, competition, sales` (numbers optional) from any keyword tool.

**9. Image model (for generate mode).** Set `[images]` model, size and quality, and check `[images.price_usd]` against OpenAI's current pricing. Generation refuses to run without a price, and `max_batch_usd` caps each batch.

**10. Finish.** Run `podagent doctor --online` (free, read-only calls) and show the table. Tell the user in plain words what is still missing. Suggest a first batch with `--dry-run`.
