# Print providers

This tool makes its own Etsy listings and mockups. A print provider only has to print and
ship orders for listings it didn't create. Adapters live in `src/podagent/providers/`.

| Adapter | Needs | What it does |
|---|---|---|
| `local_export` | nothing | Writes print files grouped by ratio and `sku_sheet.csv` to `batches/<id>/provider_export/`. Upload them to any provider by hand. |
| `browser` | Claude in Chrome and a playbook | For providers with no API. Claude follows your playbook on the provider's site and asks before any irreversible click. See [browser-adapter.md](browser-adapter.md). |
| `prodigi` | Prodigi account and API key | Uploads print files through the API and, after you publish, maps each Etsy variant to a Prodigi product by SKU. |

## Why Prodigi for the API adapter

The criteria: paper and framed art prints in many sizes, print-file upload through the API,
fulfilment of Etsy orders for listings created outside the provider (ideally by SKU), and
production in both the UK and the US. Checked in November 2026.

| | Outside Etsy listings | File upload by API | Wall art | UK and US production | Test environment |
|---|---|---|---|---|---|
| **Prodigi** | API lists synced Etsy variants (with SKU), creates mappings and turns on auto-fulfil | Yes, direct upload up to 100 MB | Paper prints and framed prints, wide size range | Own labs in UK, EU and US | Sandbox key |
| **Gelato** | Manual "Connect" per variation in the dashboard | Public URL only | Posters and framed posters | Global partner network | Draft orders only |
| **Printful** | Manual sync per variant; API sync not documented for Etsy | URL only | Posters and framed posters | US; UK site unclear for wall art | None found |
| **Printify** | Publishes its own listings; outside listings linked by hand on first order | Yes | Via third-party print partners | Varies by partner | None |

No provider matches Etsy orders by SKU on its own. Prodigi is the only one whose API lets
this tool do the matching: it reads Prodigi's copy of the listing, matches each variant's
SKU to the plan made at upload time, and creates the mapping.

**Known gap:** Prodigi's API reference doesn't say how artwork attaches to a mapped Etsy
variant. Before your first sale, open one mapped product in Prodigi's dashboard and check
the artwork is there.

## Setting up Prodigi

1. Create an account and connect your Etsy shop in Prodigi's dashboard (this step has no API).
2. Get a `sandbox_` API key first, then a `live_` key. Put it in `.env` as `PRINT_PROVIDER_API_KEY`.
   Prodigi's sandbox shares artwork and store connections with your live account.
3. Set `provider.adapter = "prodigi"` and `provider.prodigi.sales_channel_id`.
4. Fill `provider.prodigi.catalogue` with a Prodigi SKU for every size code:
   `"16x20" = ["<unframed SKU>", "<framed SKU>"]`. Check each one matches what your listing describes.
5. Run `podagent doctor --online`.

After you publish a batch's drafts in Etsy and Prodigi has synced them, run
`podagent provider-link <batch>`.

Endpoints used (API v5, `https://api.prodigi.com/v5`, `X-API-Key` header):
`POST /assets` then `PUT` to the returned upload URL; `GET /sales-channels/{id}/products`;
`GET` and `POST /sales-channels/{id}/mappings`; `GET /sales-channels/{id}/rules` and
`PATCH /sales-channels/{id}/rules/{ruleId}`; `GET /merchant` for the connection check.

## Sources

- Prodigi API v5 reference (endpoints, uploads, mappings, rules, sandbox, SKU format): https://www.prodigi.com/print-api/docs/openapi.yml
- Prodigi dashboard setup per Etsy variant: https://www.prodigi.com/etsy-print-on-demand/support/configure-your-products
- Prodigi framed print sizes and regions: https://www.prodigi.com/products/wall-art/framed-prints/classic-frames/
- Prodigi production network: https://www.prodigi.com/global-print-network/
- Gelato, connecting individual Etsy listings: https://support.gelato.com/en/articles/8996473-how-do-i-connect-individual-etsy-listings-to-gelato
- Gelato, create product from template (public file URLs): https://developers.gelato.com/docs/guides/create-product-from-template/
- Printful API: https://developers.printful.com/docs/
- Printful, adding products to Etsy: https://help.printful.com/hc/en-us/articles/25186194818204-How-do-I-add-products-to-my-Etsy-store
- Printify, importing orders with unsynced products: https://help.printify.com/hc/en-us/articles/4483617307665
