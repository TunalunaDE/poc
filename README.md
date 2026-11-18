# noknok PoC — Public Data Files

This repository hosts the **public data files** consumed by the noknok PoC app at
runtime. It is intentionally separate from the app source code (which stays private):
the app downloads these files over HTTPS, so they must be publicly readable.

## What's in here

```
manifests/
  catalog.json                  # index of all products (the app fetches this first)
  smart-lamp.json               # per-product manifests, referenced by catalog.json
  whack-a-mole.json
scripts/
  smart_lamp.py                 # product.py scripts the Pico downloads + runs
  whack_a_mole.py
```

### Compact product scripts (`scripts/src/` and `tools/compact_product.py`)

The brain keeps a product's whole source text in its RAM, so a long, well-commented
script can run out of memory on the Pico. A big product therefore keeps its readable
source in `scripts/src/<name>.py`, and the file the brain downloads,
`scripts/<name>.py`, is a generated copy without comments or docstrings:

```
python tools/compact_product.py scripts/src/smart_alarm_maxi.py scripts/smart_alarm_maxi.py
```

Edit the source, never the generated copy. The tool refuses to write if the copy
would behave differently. Small products don't need this.

The earlier trio demos (light-sound-controller, chime-box, two-button-box,
desk-lamp, desk-lamp-dimmer) were bring-up scaffolding and were retired in
Sep 2026; they remain in git history.

## How the app uses it

1. The app fetches the catalog index:
   `https://raw.githubusercontent.com/buildwithnoknok/poc/main/manifests/catalog.json`
2. `catalog.json` lists each product with the URL of its manifest (also in `manifests/`).
3. Each manifest's `files[]` entry with `dest: "product.py"` gives a `url` pointing at the
   matching script in `scripts/`. The app passes that `script_url` to the Pico during setup;
   the Pico downloads and runs it.

Adding a product = publishing a new manifest (+ script) and adding it to `catalog.json`.
No app code changes.

## Contributing a product (makers)

Everything here is public and open. A maker product is the same few files as ours, in
the same flat folders (decided in ADR-010, 18 Nov 2026):

| File | What |
| --- | --- |
| `manifests/<name>.json` | the manifest; spec: [product-manifest.md](https://github.com/buildwithnoknok/Ecosystem/blob/main/software/product-manifest.md) |
| `scripts/<name>.py` | the `product.py` the brain runs (MIT, SPDX header first line) |
| `images/<id>.webp` | optional picture: 640 x 640 px WebP, transparent background, max 60 KB |
| one line in `manifests/catalog.json` | `{ "id": "<id>", "manifest": "<raw URL of your manifest>" }` |

- **The id must be unique** in `catalog.json`: kebab-case ending in `-v1` (`my-desk-clock-v1`).
- In your manifest set `"source": "community"`. For a credit add
  `"maker": { "name": "Your Name", "url": "https://your.site" }` (`url` optional, https only).
  Leave `maker` out if you want no credit. The app shows a small "Community" label and the
  credit on the product page.
- Licence: scripts MIT, manifest and picture CC BY-SA 4.0 (see below). By opening a pull
  request you agree to that.
- **How:** fork this repo, add your files, open a pull request. The template asks for what
  we need. Without GitHub, use the upload page (coming, DEV-258).
- **Review and publishing:** automatic checks first, then a person from the noknok team
  reads the script (it runs on customers' devices). Only the noknok org account merges.
  **Merged to `main` = published**: the app reads `main`, so the product appears in every
  app on its next catalog refresh. Removing the files and the catalog line removes it again.

Authoring guide: [authoring-products.md](https://github.com/buildwithnoknok/Ecosystem/blob/main/software/authoring-products.md).

## License

- **Product scripts (`scripts/*.py`)** — [MIT](LICENSE)
- **Catalog & manifests (`manifests/*.json`)** — [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
- **Product pictures (`images/*.webp`)** — [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
