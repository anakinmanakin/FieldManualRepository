#!/usr/bin/env python3
"""Mirror the PDF/EPUB files from the T.REX ARMS Field Manual Vault.

Runs inside GitHub Actions (see .github/workflows/mirror.yml), standard library only.

How it finds manuals (public, crawler-sanctioned sources only):
  1. links on the vault's main page
  2. vault pages listed in the site's published sitemap(s) (found via robots.txt)
  3. catalog/queue.tsv, if present: a hand-off list from a previous repository
It obeys robots.txt and pauses between requests.

How it splits across repositories:
  Downloads stop once files/ reaches BUDGET_GB (default 4). Everything not yet
  downloaded is written to catalog/remaining.tsv; copy that file into the next
  repository as catalog/queue.tsv and run the workflow there.

Outputs: files/, INDEX.md, sources.tsv, catalog/all.tsv, catalog/remaining.tsv,
logs/last-run.md. Commits and pushes in batches as it goes (GitHub rejects
single pushes over 2 GB).
"""
import gzip
import html
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.robotparser

VAULT_URL = "https://www.trex-arms.com/field-manual-vault"
SITE_ROOT = "https://www.trex-arms.com"
SITE = "trex-arms.com"
UA = "FieldManualMirror/1.0 (+https://github.com/anakinmanakin/FieldManualRepository)"
EXTS = (".pdf", ".epub")
FILE_LIMIT = 95 * 1024 * 1024                       # GitHub hard cap is 100 MB per file
BUDGET = float(os.environ.get("BUDGET_GB", "4")) * 1e9
BATCH = 900e6                                       # push every ~0.9 GB
MAX_PAGES = int(os.environ.get("MAX_PAGES", "20000"))
DEADLINE = time.time() + float(os.environ.get("MAX_MINUTES", "320")) * 60
PAGE_DELAY, FILE_DELAY = 0.5, 1.0

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(ROOT, "files")
LOGS = os.path.join(ROOT, "logs")
CATALOG = os.path.join(ROOT, "catalog")
SOURCES = os.path.join(ROOT, "sources.tsv")

log_lines = []
robots = urllib.robotparser.RobotFileParser()


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


def allowed(url):
    try:
        return robots.can_fetch(UA, url)
    except Exception:
        return True


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if url.endswith(".gz") or r.headers.get("Content-Encoding") == "gzip":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        return data, r.geturl()


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


def is_vault_page(u):
    p = urllib.parse.urlparse(u)
    path = p.path.lower().rstrip("/")
    return (p.netloc.endswith(SITE) and path.startswith("/field-manual-vault/")
            and not is_doc(u) and not path.endswith(("/faq", "/citations")))


def sitemap_vault_pages():
    """Vault page URLs from the sitemaps the site publishes in robots.txt."""
    maps = list(robots.site_maps() or []) or [SITE_ROOT + "/sitemap.xml"]
    seen, pages = set(), []
    while maps and len(seen) < 500:
        sm = maps.pop(0)
        if sm in seen or SITE not in urllib.parse.urlparse(sm).netloc:
            continue
        seen.add(sm)
        try:
            xml, _ = fetch(sm, timeout=60)
        except Exception as e:
            log(f"  sitemap {sm}: {e}")
            continue
        text = xml.decode("utf-8", "replace")
        locs = [html.unescape(x).strip() for x in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text)]
        if "<sitemapindex" in text:
            maps.extend(locs)
        else:
            pages.extend(u for u in locs if is_vault_page(u))
        time.sleep(PAGE_DELAY)
    log(f"Sitemaps read: {len(seen)}; vault pages listed: {len(set(pages))}")
    return list(dict.fromkeys(pages))


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


def dir_size(path):
    return sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path))


def read_tsv(path):
    if not os.path.exists(path):
        return []
    return [l.rstrip("\n").split("\t") for l in open(path, encoding="utf-8") if l.strip()]


def write_tsv(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(r) + "\n")


def git_push(msg):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    subprocess.run(["git", "add", "-A", "files", "INDEX.md", "sources.tsv", "catalog", "logs"], cwd=ROOT)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "-q"], cwd=ROOT, check=True)
    log(f"  pushed: {msg}")


def listing_key(u, branch):
    """Canonical key for a branch listing page, or None if it isn't one we follow.
    Only follows the same branch plus numeric params (pagination), so filter
    combinations can't multiply."""
    p = urllib.parse.urlparse(u)
    if not p.netloc.endswith(SITE) or p.path.rstrip("/").lower() != "/field-manual-vault":
        return None
    q = urllib.parse.parse_qsl(p.query)
    if dict(q).get("branch") != branch:
        return None
    for k, v in q:
        if k != "branch" and not v.isdigit():
            return None
    return "https://www.trex-arms.com/field-manual-vault?" + urllib.parse.urlencode(sorted(q))


def crawl_listings(main_html, main_url):
    """Walk each branch listing (and its pagination) to collect every document page."""
    branches = []
    for u in extract_urls(main_html, main_url):
        b = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(u).query)).get("branch")
        if b and b not in branches:
            branches.append(b)
    log(f"Branch listings: {', '.join(branches) or 'none found'}")
    pages = []
    for b in branches:
        start = listing_key(f"{VAULT_URL}?branch={b}", b)
        queue, seen, found = [start], set(), 0
        while queue and len(seen) < 3000 and time.time() < DEADLINE - 3600:
            u = queue.pop(0)
            if u in seen:
                continue
            seen.add(u)
            if not allowed(u):
                log(f"  robots.txt disallows {u}")
                continue
            try:
                body, final = fetch(u, timeout=60)
            except Exception as e:
                log(f"  skip listing {u}: {e}")
                continue
            for link in extract_urls(body.decode("utf-8", "replace"), final):
                if is_vault_page(link):
                    pages.append(link.rstrip("/"))
                    found += 1
                else:
                    k = listing_key(link, b)
                    if k and k not in seen:
                        queue.append(k)
            time.sleep(PAGE_DELAY)
        log(f"  {b}: {len(seen)} listing pages read")
    pages = list(dict.fromkeys(pages))
    log(f"Document pages found via listings: {len(pages)}")
    return pages


def discover():
    """Return ordered {doc_url: source_page}. Progress is saved in catalog/ so a
    scan that runs out of time resumes on the next run."""
    docs = {}
    queue = read_tsv(os.path.join(CATALOG, "queue.tsv"))
    if queue:
        log(f"Using hand-off list catalog/queue.tsv: {len(queue)} files")
        for row in queue:
            docs[row[0]] = row[1] if len(row) > 1 else ""
        return docs

    pages_file = os.path.join(CATALOG, "pages.txt")
    done_file = os.path.join(CATALOG, "pages-done.txt")
    for row in read_tsv(os.path.join(CATALOG, "all.tsv")):
        docs[row[0]] = row[1] if len(row) > 1 else ""
    pages = [r[0] for r in read_tsv(pages_file)]
    done = {r[0] for r in read_tsv(done_file)}

    if not pages or os.environ.get("REFRESH_CATALOG") == "1":
        log(f"Fetching {VAULT_URL}")
        body, final = fetch(VAULT_URL)
        page = body.decode("utf-8", "replace")
        with open(os.path.join(LOGS, "vault-page.html"), "w", encoding="utf-8") as f:
            f.write(page)
        urls = extract_urls(page, final)
        for u in urls:
            if is_doc(u):
                docs.setdefault(u, VAULT_URL)
        found = [u.rstrip("/") for u in urls if is_vault_page(u)]
        found += crawl_listings(page, final)
        found += sitemap_vault_pages()
        pages = list(dict.fromkeys(pages + [p.rstrip("/") for p in found]))[:MAX_PAGES]
        write_tsv(pages_file, [[p] for p in pages])
    else:
        log(f"Resuming saved catalog: {len(pages)} document pages, {len(done)} already scanned")

    todo = [p for p in pages if p not in done]
    log(f"Document pages to scan: {len(todo)}")
    with open(done_file, "a", encoding="utf-8") as done_out:
        for i, sp in enumerate(todo, 1):
            if time.time() > DEADLINE - 3600:
                log("  pausing page scan to leave time for downloads; it resumes next run")
                break
            if not allowed(sp):
                log(f"  robots.txt disallows {sp}")
                done_out.write(sp + "\n")
                continue
            try:
                b, f2 = fetch(sp, timeout=60)
                for u in extract_urls(b.decode("utf-8", "replace"), f2):
                    if is_doc(u):
                        docs.setdefault(u, sp)
                done_out.write(sp + "\n")
            except Exception as e:
                log(f"  skip page {sp}: {e}")
            if i % 250 == 0:
                log(f"  scanned {i}/{len(todo)} pages, {len(docs)} files known")
                done_out.flush()
                write_tsv(os.path.join(CATALOG, "all.tsv"), [[u, s] for u, s in docs.items()])
            time.sleep(PAGE_DELAY)
    return docs


def main():
    for d in (FILES, LOGS, CATALOG):
        os.makedirs(d, exist_ok=True)
    robots.set_url(SITE_ROOT + "/robots.txt")
    try:
        robots.read()
        log("robots.txt loaded")
    except Exception as e:
        log(f"robots.txt not readable ({e}); proceeding politely")

    try:
        docs = discover()
    except Exception as e:
        log(f"ERROR during discovery: {e}")
        docs = {}
    log(f"Total PDF/EPUB files known: {len(docs)}")
    write_tsv(os.path.join(CATALOG, "all.tsv"), [[u, s] for u, s in docs.items()])

    have = {r[0]: r for r in read_tsv(SOURCES) if len(r) >= 3}
    # Keep every file already mirrored, even if this run couldn't re-confirm it.
    got = [r for r in have.values() if os.path.exists(os.path.join(FILES, r[1]))]
    got_urls = {r[0] for r in got}
    used = {n.lower() for n in os.listdir(FILES)}
    size = dir_size(FILES)
    since_push = 0
    big, failed, blocked, remaining = [], [], [], []
    stop_reason = ""

    for n, (u, src) in enumerate(docs.items(), 1):
        if u in got_urls:
            continue
        if stop_reason:
            remaining.append([u, src])
            continue
        if size >= BUDGET:
            stop_reason = f"size budget reached ({size/1e9:.2f} GB)"
            remaining.append([u, src])
            continue
        if time.time() > DEADLINE:
            stop_reason = "time limit reached; run the workflow again to continue"
            remaining.append([u, src])
            continue
        if not allowed(u):
            blocked.append(u)
            continue
        try:
            data, _ = fetch(u, timeout=300)
        except Exception as e:
            log(f"[{n}/{len(docs)}] FAILED {u}: {e}")
            failed.append((u, str(e)))
            continue
        if len(data) > FILE_LIMIT:
            log(f"[{n}/{len(docs)}] TOO LARGE ({len(data)/1e6:.0f} MB) {u}")
            big.append((u, len(data)))
            continue
        name = safe_name(u, used)
        with open(os.path.join(FILES, name), "wb") as f:
            f.write(data)
        got.append([u, name, src])
        got_urls.add(u)
        size += len(data)
        since_push += len(data)
        log(f"[{n}/{len(docs)}] saved {name} ({len(data)/1e6:.1f} MB, repo {size/1e9:.2f} GB)")
        if since_push >= BATCH:
            write_tsv(SOURCES, got)
            finish(docs, got, big, failed, blocked, remaining, "in progress")
            git_push(f"Add manuals (repo now {size/1e9:.2f} GB)")
            since_push = 0
        time.sleep(FILE_DELAY)

    if stop_reason:
        log(f"Stopped: {stop_reason}. {len(remaining)} files left for the next run/repository.")
    write_tsv(SOURCES, got)
    write_tsv(os.path.join(CATALOG, "remaining.tsv"), remaining)
    finish(docs, got, big, failed, blocked, remaining, stop_reason or "complete")
    git_push("Update mirrored field manuals")


def finish(docs, got, big, failed, blocked, remaining, status):
    with open(os.path.join(ROOT, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("# Index of mirrored manuals\n\n")
        total = sum(os.path.getsize(os.path.join(FILES, r[1])) for r in got
                    if os.path.exists(os.path.join(FILES, r[1])))
        f.write(f"{len(got)} files, {total/1e9:.2f} GB. Source: <{VAULT_URL}>\n\n")
        if remaining:
            f.write(f"**{len(remaining)} more files** are listed in "
                    "[catalog/remaining.tsv](catalog/remaining.tsv) for the next repository.\n\n")
        f.write("| File | Size | Original link |\n|---|---|---|\n")
        for u, name, _ in sorted(got, key=lambda r: r[1].lower()):
            p = os.path.join(FILES, name)
            mb = os.path.getsize(p) / 1e6 if os.path.exists(p) else 0
            f.write(f"| [{name}](files/{urllib.parse.quote(name)}) | {mb:.1f} MB | [source]({u}) |\n")
        if big:
            f.write("\n## Not mirrored: over GitHub's 100 MB file limit\n\n")
            f.writelines(f"- <{u}> ({s/1e6:.0f} MB)\n" for u, s in big)
        if failed:
            f.write("\n## Not mirrored: download failed\n\n")
            f.writelines(f"- <{u}> — {e}\n" for u, e in failed)
        if blocked:
            f.write("\n## Not mirrored: disallowed by the site's robots.txt\n\n")
            f.writelines(f"- <{u}>\n" for u in blocked)
    with open(os.path.join(LOGS, "last-run.md"), "w", encoding="utf-8") as f:
        f.write(f"# Last run: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} ({status})\n\n")
        f.write(f"- Files known: {len(docs)}\n- In this repo: {len(got)}\n"
                f"- Left for next repo: {len(remaining)}\n- Too large: {len(big)}\n"
                f"- Failed: {len(failed)}\n- Disallowed by robots.txt: {len(blocked)}\n\n```\n")
        f.write("\n".join(log_lines[-3000:]))
        f.write("\n```\n")


if __name__ == "__main__":
    main()
