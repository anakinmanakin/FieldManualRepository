#!/usr/bin/env python3
"""Mirror the PDF/EPUB files linked from the T.REX ARMS Field Manual Vault.

Runs inside GitHub Actions (see .github/workflows/mirror.yml). Uses only the
Python standard library. Writes:
  files/            downloaded manuals
  INDEX.md          table of files, sizes and source links
  logs/last-run.md  what happened on the most recent run
"""
import html
import os
import re
import sys
import time
import urllib.parse
import urllib.request

VAULT_URL = "https://www.trex-arms.com/field-manual-vault"
SITE = "trex-arms.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
EXTS = (".pdf", ".epub")
GITHUB_LIMIT = 95 * 1024 * 1024          # stay safely under GitHub's 100 MB cap
MAX_SUBPAGES = 300                       # cap on linked pages crawled one level deep
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(ROOT, "files")
LOGS = os.path.join(ROOT, "logs")

log_lines = []


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl()


def extract_urls(text, base):
    found = re.findall(r'(?:href|src|data-href|data-url)\s*=\s*["\']([^"\']+)["\']', text)
    found += re.findall(r'https?:\\?/\\?/[^\s"\'<>)]+', text)
    out = []
    for raw in found:
        u = html.unescape(raw).replace("\\/", "/")
        u = urllib.parse.urljoin(base, u).split("#")[0]
        if u.startswith("http"):
            out.append(u)
    return out


def is_doc(u):
    return urllib.parse.urlparse(u).path.lower().endswith(EXTS)


def is_site_page(u):
    p = urllib.parse.urlparse(u)
    if not p.netloc.endswith(SITE) or is_doc(u):
        return False
    path = p.path.lower()
    skip = ("/cart", "/checkout", "/account", "/login", "/wp-admin", "/wp-json",
            "/feed", ".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".svg",
            ".gif", ".ico", ".woff", ".xml")
    return not any(s in path for s in skip)


def safe_name(url, used):
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path))
    name = re.sub(r'[^\w.\- ()]+', "_", name).strip() or "file"
    stem, ext = os.path.splitext(name)
    out, n = name, 2
    while out.lower() in used:
        out = f"{stem}_{n}{ext}"
        n += 1
    used.add(out.lower())
    return out


def main():
    os.makedirs(FILES, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)

    log(f"Fetching {VAULT_URL}")
    try:
        body, final = fetch(VAULT_URL)
    except Exception as e:
        log(f"ERROR: could not load vault page: {e}")
        return finish({}, [], [], [])
    page = body.decode("utf-8", "replace")
    log(f"Vault page loaded ({len(page):,} bytes, final URL {final})")
    with open(os.path.join(LOGS, "vault-page.html"), "w", encoding="utf-8") as f:
        f.write(page)

    urls = extract_urls(page, final)
    docs = {u: VAULT_URL for u in urls if is_doc(u)}
    log(f"Direct PDF/EPUB links on vault page: {len(docs)}")

    # One level deeper: manual/product pages linked from the vault often hold the download link.
    subpages = []
    for u in urls:
        if is_site_page(u) and u.rstrip("/") != VAULT_URL.rstrip("/") and u not in subpages:
            subpages.append(u)
    subpages = subpages[:MAX_SUBPAGES]
    log(f"Checking {len(subpages)} linked pages on {SITE} for more downloads")
    for i, sp in enumerate(subpages, 1):
        try:
            b, f2 = fetch(sp, timeout=60)
            for u in extract_urls(b.decode("utf-8", "replace"), f2):
                if is_doc(u) and u not in docs:
                    docs[u] = sp
        except Exception as e:
            log(f"  skip page {sp}: {e}")
        time.sleep(0.5)
    log(f"Total unique PDF/EPUB links found: {len(docs)}")

    used = {n.lower() for n in os.listdir(FILES)}
    existing = {}
    idx = os.path.join(ROOT, "sources.tsv")
    if os.path.exists(idx):
        for line in open(idx, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                existing[parts[0]] = (parts[1], parts[2])

    got, big, failed = [], [], []
    for n, (u, page_src) in enumerate(sorted(docs.items()), 1):
        if u in existing and os.path.exists(os.path.join(FILES, existing[u][0])):
            got.append((existing[u][0], u, page_src))
            continue
        name = safe_name(u, used)
        dest = os.path.join(FILES, name)
        try:
            data, _ = fetch(u, timeout=300)
        except Exception as e:
            log(f"[{n}/{len(docs)}] FAILED {u}: {e}")
            failed.append((u, str(e)))
            used.discard(name.lower())
            continue
        if len(data) > GITHUB_LIMIT:
            log(f"[{n}/{len(docs)}] TOO LARGE ({len(data)/1e6:.0f} MB) {u}")
            big.append((u, len(data)))
            used.discard(name.lower())
            continue
        with open(dest, "wb") as f:
            f.write(data)
        log(f"[{n}/{len(docs)}] saved {name} ({len(data)/1e6:.1f} MB)")
        got.append((name, u, page_src))
        time.sleep(1)

    with open(idx, "w", encoding="utf-8") as f:
        for name, u, src in got:
            f.write(f"{u}\t{name}\t{src}\n")
    finish(docs, got, big, failed)


def finish(docs, got, big, failed):
    with open(os.path.join(ROOT, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("# Index of mirrored manuals\n\n")
        f.write(f"{len(got)} files. Source: <{VAULT_URL}>\n\n")
        f.write("| File | Size | Original link |\n|---|---|---|\n")
        for name, u, _ in sorted(got, key=lambda r: r[0].lower()):
            p = os.path.join(FILES, name)
            size = os.path.getsize(p) / 1e6 if os.path.exists(p) else 0
            f.write(f"| [{name}](files/{urllib.parse.quote(name)}) | {size:.1f} MB | [source]({u}) |\n")
        if big:
            f.write("\n## Not mirrored: over GitHub's 100 MB file limit\n\n")
            for u, s in big:
                f.write(f"- <{u}> ({s/1e6:.0f} MB)\n")
        if failed:
            f.write("\n## Not mirrored: download failed\n\n")
            for u, e in failed:
                f.write(f"- <{u}> — {e}\n")
    with open(os.path.join(LOGS, "last-run.md"), "w", encoding="utf-8") as f:
        f.write(f"# Last run: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n\n")
        f.write(f"- Links found: {len(docs)}\n- Mirrored: {len(got)}\n"
                f"- Too large: {len(big)}\n- Failed: {len(failed)}\n\n```\n")
        f.write("\n".join(log_lines))
        f.write("\n```\n")


if __name__ == "__main__":
    main()
