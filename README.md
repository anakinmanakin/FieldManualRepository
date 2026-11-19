# Field Manual Repository

A mirror of the original public-domain military field manuals (PDF and EPUB) that
T.REX ARMS makes freely available in its
[Field Manual Vault](https://www.trex-arms.com/field-manual-vault).

- **[INDEX.md](INDEX.md)** lists every file with its size and original link.
- **`files/`** holds the manuals.
- **`logs/last-run.md`** shows what the most recent update did.

## Status

This repository holds the 25 manuals featured on the vault's main page, each as a
PDF and an EPUB. The vault lists about 6,500 publications in total, but the site's
robots.txt disallows automated access to its category and manual pages, so the
rest of the library is not mirrored here.

The workflow in `.github/workflows/mirror.yml` (`scripts/fetch_vault.py`) follows
robots.txt and only runs when started by hand from the **Actions** tab.

## Note on rights

The originals are U.S. government publications, which are generally in the public
domain. T.REX ARMS's own improved reprint editions are sold separately and are
not part of this mirror. Check the status of any individual file before reuse.
