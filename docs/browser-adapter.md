# Browser adapter

For print providers with no API. Python prepares the files; Claude works through your
provider's website with the Claude in Chrome extension, following a playbook you write.

## How it works

1. `podagent run <batch>` writes the print files and `sku_sheet.csv` to
   `batches/<id>/provider_export/`, plus `browser_tasks.json`. That file groups the files by
   print ratio and splits them into upload calls that stay under the extension's 10 MB limit.
2. Claude opens your provider's site in Chrome and follows the playbook for each group.
3. Before any step marked `[ASK]`, Claude stops and asks you. Mark every step that creates,
   saves, submits, publishes or deletes something.

Set `provider.adapter = "browser"` and `provider.browser_playbook = "playbooks/my-provider.md"`.

## Writing a playbook

Plain numbered steps, one action each, naming the button or field as it appears on screen:

```markdown
# My provider: add products by bulk upload

For each ratio group in browser_tasks.json:
1. Open https://provider.example.com/products and click "Bulk upload".
2. Choose the size group that matches the ratio (e.g. 4x5 -> "8x10 and 16x20").
3. Find the file input and upload the files in each entry of upload_calls, one call per entry.
4. Check every file shows "Ready". Re-upload any that show an error, one at a time.
5. [ASK] Click "Create products".
6. Wait until the product count goes up by the number of files.
7. For each product, set the SKU from sku_sheet.csv. [ASK] Click "Save".
```

Tips:
- Name files by SKU (the adapter already does), so the provider's product names match.
- Keep the Chrome window visible while Claude works: hidden tabs pause.
- If a page doesn't match the playbook, Claude stops and asks rather than guessing.

## Example: bulk upload at a wall-art printer

The playbook format was modelled on the bulk-upload flow at PrintShrimp, a UK wall-art
printer with no API that matches Etsy orders by SKU. It has not been run from this repo,
and the site may have changed since. Check each step against the live site before relying on it.

```markdown
# PrintShrimp: add a batch

First size group (A-series):
1. Open https://app.printshrimp.com/products. Click "Bulk Upload", then "Get started".
2. Choose "A-series", then "Next", then "Next".
3. Find the file input and upload the files for the "iso" group, one call per upload_calls entry.
4. Check every SKU shows "Ready to upload".
5. [ASK] Click "Create products".
6. Wait until "Sizes added" equals the number of files.

Each other ratio group (2:3, 3:4, 4:5, 5:7, 11:14):
1. Click "Bulk Add Sizes", then "Get started", then choose the matching group.
2. Upload that group's files as above and check each shows "Ready to upload".
3. [ASK] Click "Add missing sizes".
4. Re-upload any file marked "Needs attention" on its own.
```
