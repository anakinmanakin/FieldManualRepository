#!/usr/bin/env python3
"""Download the files listed in catalog/manifest.tsv (or catalog/queue.tsv, a hand-off
list from a previous repository) into library/<topic>/.

- Obeys each host's robots.txt and pauses between downloads.
- Stops when files/ + library/ reach BUDGET_GB (default 4) and writes everything
  not yet downloaded to catalog/remaining.tsv. To continue in a new repository,
  copy scripts/ and .github/ there, save catalog/remaining.tsv as catalog/queue.tsv,
  and run the "Download library" workflow.
- Commits and pushes in ~0.9 GB batches (GitHub rejects single pushes over 2 GB).
"""
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.robotparser

UA = "FieldManualMirror/1.0 (+https://github.com/anakinmanakin/FieldManualRepository)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "catalog")
LIB = os.path.join(ROOT, "library")
LOGS = os.path.join(ROOT, "logs")
DONE = os.path.join(CATALOG, "downloaded.tsv")
BUDGET = float(os.environ.get("BUDGET_GB", "4")) * 1e9
BATCH = 900e6
DEADLINE = time.time() + float(os.environ.get("MAX_MINUTES", "320")) * 60
FILE_LIMIT = 95 * 1024 * 1024
DELAY = 1.0
HEADER = "url\tfolder\tidentifier\tsize\tbasis\ttitle"

robots = {}
log_lines = []


def log(m):
    print(m, flush=True)
    log_lines.append(m)


def allowed(url):
    p = urllib.parse.urlparse(url)
    host = f"{p.scheme}://{p.netloc}"
    if host not in robots:
        rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        robots[host] = rp
    rp = robots[host]
    return True if rp is None else rp.can_fetch(UA, url)


def tree_size(*dirs):
    total = 0
    for d in dirs:
        for base, _, files in os.walk(d):
            total += sum(os.path.getsize(os.path.join(base, f)) for f in files)
    return total


def read_rows(path):
    if not os.path.exists(path):
        return []
    rows = [l.rstrip("\n").split("\t") for l in open(path, encoding="utf-8") if l.strip()]
    return [r for r in rows if r and r[0] != "url"]


def write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")


def git_push(msg):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    subprocess.run(["git", "add", "-A", "library", "catalog", "LIBRARY.md", "logs"], cwd=ROOT)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=ROOT, check=True)
    subprocess.run(["git", "pull", "-q", "--rebase"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "-q"], cwd=ROOT, check=True)
    log(f"  pushed: {msg}")


NAMES = {}


def clean_title(title):
    t = re.sub(r"[\\/:*?\"<>|\t\r\n]+", " - ", title)
    t = re.sub(r"\s*-\s*(-\s*)+", " - ", t)
    t = re.sub(r"\s+", " ", t).strip(" .-_")
    return t[:120].rstrip(" .-_") or "Untitled"


def assign_names(rows):
    """Readable, unique, stable file names: <topic>/<Title>.<ext>, numbered on clashes."""
    owner = {}
    for r in rows:
        if r[0] in NAMES:
            continue
        ext = os.path.splitext(urllib.parse.unquote(r[0]))[1].lower() or ".pdf"
        base = os.path.join(r[1], clean_title(r[5]))
        n, name = 1, base
        while (name.lower(), ext) in owner and owner[(name.lower(), ext)] != r[2]:
            n += 1
            name = f"{base} ({n})"
        owner[(name.lower(), ext)] = r[2]
        NAMES[r[0]] = name + ext


def local_name(row):
    if row[0] not in NAMES:
        assign_names([row])
    return NAMES[row[0]]


def source_link(r):
    if r[2].startswith("http"):
        host = urllib.parse.urlparse(r[2]).netloc.replace("www.", "")
        return f"[{host}]({r[2]})"
    return f"[archive.org](https://archive.org/details/{r[2]})"


def write_index(done, remaining, skipped):
    by_folder = {}
    for r in done:
        by_folder.setdefault(r[1], []).append(r)
    total = tree_size(LIB)
    with open(os.path.join(ROOT, "LIBRARY.md"), "w", encoding="utf-8") as f:
        f.write("# Survival & off-grid library\n\n")
        f.write(f"{len(done)} files, {total/1e9:.2f} GB, sorted into topic folders under library/. "
                "Public-domain basis per file: **usgov** = US federal government publication, "
                "**pre1931** = published 1930 or earlier, **pdmark** = marked public domain.\n\n")
        if remaining:
            f.write(f"**{len(remaining)} more files** are listed in "
                    "[catalog/remaining.tsv](catalog/remaining.tsv) for the next repository.\n\n")
        for folder in sorted(by_folder):
            items = {}
            for r in by_folder[folder]:
                items.setdefault((r[5].lower(), r[2]), []).append(r)
            f.write(f"\n## {folder} ({len(items)})\n\n| Title | Files | Basis | Source |\n|---|---|---|---|\n")
            for key in sorted(items):
                group = sorted(items[key], key=lambda r: local_name(r).lower().endswith(".epub"))
                r0 = group[0]
                links = " · ".join(
                    f"[{os.path.splitext(local_name(r))[1][1:].upper()}](library/{urllib.parse.quote(local_name(r))})"
                    for r in group)
                f.write(f"| {r0[5].replace('|', '/')} | {links} | {r0[4]} | {source_link(r0)} |\n")
        if skipped:
            f.write("\n## Skipped\n\n")
            f.writelines(f"- {u} — {why}\n" for u, why in skipped)


def main():
    os.makedirs(LIB, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    queue = read_rows(os.path.join(CATALOG, "queue.tsv"))
    source = "queue.tsv" if queue else "manifest.tsv"
    rows = queue or read_rows(os.path.join(CATALOG, "manifest.tsv"))
    done = read_rows(DONE)
    done_urls = {r[0] for r in done}
    assign_names(done + rows)   # already-downloaded files keep their names
    log(f"{len(rows)} files in catalog/{source}; {len(done)} already downloaded")

    size = tree_size(LIB, os.path.join(ROOT, "files"))
    since = 0
    remaining, skipped, stop = [], [], ""
    for i, r in enumerate(rows, 1):
        if r[0] in done_urls:
            continue
        if stop:
            remaining.append(r)
            continue
        if size >= BUDGET:
            stop = f"size budget reached ({size/1e9:.2f} GB)"
        elif time.time() > DEADLINE:
            stop = "time limit reached; run the workflow again to continue"
        if stop:
            remaining.append(r)
            continue
        if not allowed(r[0]):
            skipped.append((r[0], "disallowed by robots.txt"))
            continue
        dest = os.path.join(LIB, local_name(r))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            req = urllib.request.Request(r[0], headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read(FILE_LIMIT + 1)
        except Exception as e:
            skipped.append((r[0], f"download failed: {e}"))
            log(f"[{i}/{len(rows)}] FAILED {r[0]}: {e}")
            continue
        if len(data) > FILE_LIMIT:
            skipped.append((r[0], "over 95 MB"))
            continue
        with open(dest, "wb") as f:
            f.write(data)
        done.append(r)
        done_urls.add(r[0])
        size += len(data)
        since += len(data)
        log(f"[{i}/{len(rows)}] {local_name(r)} ({len(data)/1e6:.1f} MB, repo {size/1e9:.2f} GB)")
        if since >= BATCH:
            write_rows(DONE, done)
            write_index(done, [], skipped)
            git_push(f"Add library files (repo {size/1e9:.2f} GB)")
            since = 0
        time.sleep(DELAY)

    if stop:
        log(f"Stopped: {stop}. {len(remaining)} files left.")
    write_rows(DONE, done)
    write_rows(os.path.join(CATALOG, "remaining.tsv"), remaining)
    write_index(done, remaining, skipped)
    with open(os.path.join(LOGS, "library-run.md"), "w", encoding="utf-8") as f:
        f.write(f"# Library run {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n\n"
                f"- Downloaded so far: {len(done)}\n- Left for next repo: {len(remaining)}\n"
                f"- Skipped this run: {len(skipped)}\n- Stop reason: {stop or 'finished list'}\n\n```\n"
                + "\n".join(log_lines[-3000:]) + "\n```\n")
    git_push("Update survival & off-grid library")


if __name__ == "__main__":
    main()
