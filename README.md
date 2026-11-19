# Field Manual Repository

A mirror of the original public-domain military field manuals (PDF and EPUB) that
T.REX ARMS makes freely available in its
[Field Manual Vault](https://www.trex-arms.com/field-manual-vault).

- **[INDEX.md](INDEX.md)** lists every file with its size and original link.
- **`files/`** holds the manuals.
- **`logs/last-run.md`** shows what the most recent update did.

## How it updates

A GitHub Action (`.github/workflows/mirror.yml`) runs `scripts/fetch_vault.py`
every Monday and commits any new manuals. To run it now: **Actions** tab →
**Mirror Field Manual Vault** → **Run workflow**.

Files over GitHub's 100 MB limit are not stored; they're listed in INDEX.md
with their original links.

## Note on rights

The originals are U.S. government publications, which are generally in the public
domain. T.REX ARMS's own improved reprint editions are sold separately and are
not part of this mirror. Check the status of any individual file before reuse.
