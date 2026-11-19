# Field Manual Repository

A library of public-domain survival, off-grid and field manuals, sorted by topic
and named by title. See **[LIBRARY.md](LIBRARY.md)** for the full list with
links to each file and its source.

## Layout

```
library/
  survival/              medical-first-aid/     water/
  food-preservation/     foraging-hunting/      farming-gardening/
  navigation-fieldcraft/ shelter-building/      energy-tools/
  comms/                 tactics-training/      fitness-leadership/
```

## Where the files come from

- **T.REX ARMS Field Manual Vault** — the 25 manuals featured on its main page,
  each as PDF and EPUB. The site's robots.txt disallows automated access to the
  rest of the vault, so nothing more is taken from it.
- **Internet Archive** — found through its official search API and kept only
  with a clear public-domain basis:
  - `usgov` — published by a US federal government body
  - `pre1931` — published in 1930 or earlier
  - `pdmark` — marked public domain by the Archive

The basis is a best-effort reading of each item's catalog record. Check any
individual file before relying on its status.

## How it works

Two GitHub Actions, both started by hand from the **Actions** tab:

1. **Build library catalog** (`scripts/build_catalog.py`) searches the Internet
   Archive by topic and writes `catalog/manifest.tsv` (what to download) and
   `catalog/candidates.tsv` (every hit, with why it was kept or rejected).
2. **Download library** (`scripts/mirror_manifest.py`) downloads the manifest
   into `library/<topic>/`, follows each site's robots.txt, and pushes in batches.

## Continuing in another repository

The job stops when this repository reaches about 4 GB and writes everything it
hasn't downloaded to `catalog/remaining.tsv`. To continue in a new repository,
copy `scripts/` and `.github/` there, save `catalog/remaining.tsv` as
`catalog/queue.tsv`, and run **Download library**.
