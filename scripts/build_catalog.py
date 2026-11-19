#!/usr/bin/env python3
"""Build a catalog of public-domain survival / off-grid manuals from the Internet Archive.

Uses the Archive's official bulk search API (services/search/v1/scrape) and item
metadata API. Nothing is downloaded here except metadata. Output:
  catalog/manifest.tsv   files to mirror, in priority order
  catalog/candidates.tsv every search hit with the reason it was kept or rejected
  logs/catalog-run.md    summary

An item is kept only with a clear public-domain basis:
  usgov   - published by a US federal government body (17 U.S.C. 105)
  pre1931 - published in 1930 or earlier (public domain in the US as of 2026)
Uploader-applied "public domain" marks are not trusted on their own.
"""
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

UA = "FieldManualMirror/1.0 (+https://github.com/anakinmanakin/FieldManualRepository)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "catalog")
LOGS = os.path.join(ROOT, "logs")
FILE_LIMIT = 95 * 1024 * 1024
MAX_PER_TOPIC = int(os.environ.get("MAX_PER_TOPIC", "600"))

# Topic folders, in download priority order. Each is (folder, search terms).
TOPICS = [
    ("survival", ['survival', '"escape and evasion"', 'evasion', '"survival manual"',
                  '"sea survival"', '"arctic survival"', '"jungle survival"', '"desert survival"']),
    ("medical-first-aid", ['"first aid"', '"field medicine"', '"emergency war surgery"',
                           '"medicine chest"', '"field sanitation"', '"combat lifesaver"',
                           '"emergency medical"', '"home nursing"']),
    ("water", ['"water purification"', '"water supply"', '"well drilling"', '"water wells"',
               '"water treatment"', '"cisterns"']),
    ("food-preservation", ['canning', '"food preservation"', '"home canning"', '"drying foods"',
                           '"curing meat"', '"smoking meat"', '"root cellar"', '"food storage"']),
    ("foraging-hunting", ['"edible plants"', '"edible wild"', 'foraging', 'trapping', 'trapper',
                          '"wild game"', 'fishing', 'hunting']),
    ("farming-gardening", ['"farmers\' bulletin"', '"vegetable garden"', 'gardening', 'poultry',
                           'beekeeping', '"bee keeping"', 'livestock', '"small farm"', 'homestead',
                           '"soil conservation"', 'orchard']),
    ("navigation-fieldcraft", ['"map reading"', '"land navigation"', 'orienteering', 'fieldcraft',
                               'woodcraft', 'camping', 'mountaineering', '"cold weather operations"',
                               '"knots"', '"rope"', '"camouflage"']),
    ("shelter-building", ['"log cabin"', 'carpentry', 'masonry', 'adobe', '"rammed earth"',
                          '"building construction"', '"rural buildings"', 'plumbing']),
    ("energy-tools", ['windmill', '"water wheel"', '"wind power"', '"solar energy"', 'blacksmithing',
                      'forging', '"small engine"', '"wood fuel"', 'charcoal', 'tanning', 'leather']),
    ("comms", ['"radio operator"', '"field radio"', '"radio communication"', '"antenna"',
               '"signal communication"']),
]

TITLE_RE = {
    "survival": r"\bsurviv(al|e|ing)\b(?!\s+(of|and growth|rates?|analysis|probabilit\w*|times?|curves?|data|studies|function))|\bsere\b|\bevasion\b|fallout|civil defen[cs]e|emergency (preparedness|shelter)|bomb shelter",
    "medical-first-aid": r"first[- ]aid|field medicine|war surgery|medicine chest|sanitation|combat lifesaver|emergency (medic\w*|care)|home nursing|medical (handbook|guide)|\bmedic\b|wound care|hygiene",
    "water": r"water (purification|supply|supplies|treatment|wells?|sources?|storage|disinfection)|well[- ]drilling|\bwells\b|cisterns?|springs? development|solar still",
    "food-preservation": r"canning|canned|preserv(e|es|ing|ation)|drying (fruits?|vegetables?|foods?)|dried (fruits?|foods?)|\bcuring\b|cured meat|smok(e|ed|ing) (meat|fish)|root cellar|food storage|home storage|freezing (foods?|fruits?)|pickl\w*|jams?\b|jellies",
    "foraging-hunting": r"\bedible\b|wild (foods?|plants|game|fruits?)|forag\w*|trap(ping|per|s)\b|\bhunting\b|\bfishing\b|fish culture|game (birds|animals)|mushrooms?|\bnuts\b|berries",
    "farming-gardening": r"farmers'? bulletin|garden\w*|vegetables?|poultry|chickens?|bee[- ]?keeping|\bbees\b|\bhoney\b|livestock|\bgoats?\b|\bsheep\b|\bhogs?\b|swine|dairy|small farm|homestead\w*|\bsoils?\b|orchard|fruit (growing|trees)|\bcrops?\b|seed saving|compost\w*|irrigation",
    "navigation-fieldcraft": r"map reading|land navigation|compass|orienteering|fieldcraft|woodcraft|camp(ing|craft|fire)|mountaineering|cold[- ]weather|\bknots?\b|\bropes?\b|camouflage|scouting|tracking|outdoor|wilderness",
    "shelter-building": r"log (cabins?|house|buildings)|carpentry|masonry|adobe|rammed earth|building construction|farm buildings|rural buildings|plumbing|concrete|house construction|\bshelters?\b|\bbarns?\b|\bfences?\b|frame construction|roofing",
    "energy-tools": r"windmills?|water ?wheels?|wind power|\bsolar\b|blacksmith\w*|\bforg(e|ing)\b|small engines?|wood fuel|firewood|charcoal|tann(ing|ed)|leather|hand tools?|sharpening|welding|\bstoves?\b|electric(al)? wiring|generators?",
    "comms": r"\bradio\b|antennas?|signal communication|field telephone|\bmorse\b|communications? (manual|handbook)",
}
TITLE_RE = {k: re.compile(v, re.I) for k, v in TITLE_RE.items()}
EXCLUDE_TITLE = re.compile(
    r"\b(rats?|mice|mouse|toxicity|toxic potential|carcinogen\w*|bioassay|statistical report|"
    r"retirement system|seedlings?|conifers?|salmon|microorganisms?|bacteria\w*|cancer|patients?|"
    r"clinical|tumou?rs?|cell survival|missiles?|vulnerability|insurgen\w*|novel|poems?|weekly|"
    r"annual report|congress\w*|hearings?|budget|cthulhu|role-?playing|d20|dungeons?|"
    r"statistics|thesis|dissertation)\b", re.I)
DESIGNATION = re.compile(
    r"\b(FM|TM|TC|ATP|ADP|ADRP|AFH|AFM|AFR|AFMAN|AFPAM|AFTTP|MCRP|MCWP|MCTP|MCDP|NAVEDTRA|"
    r"NAVPERS|NAVMED|DA PAM|GTA|ST)\s?-?\d", re.I)
GOV_SERIES = re.compile(r"farmers'? bulletin|home and garden bulletin|agriculture (handbook|information bulletin)|"
                        r"leaflet no|miscellaneous publication|technical bulletin|circular no", re.I)
GOV_CREATOR = re.compile(r"^\s*(united states|u\.\s?s\.)[\s.,]|^\s*(department|dept\.?) of|^\s*headquarters",
                         re.I)
GOV = re.compile(
    r"department of the (army|navy|air force|interior|agriculture)|war department|"
    r"\bu\.?\s?s\.?\s?(army|navy|air force|marine|dept|department|government|govt|forest service|"
    r"public health)|united states (army|navy|air force|marine|department|government|forest)|"
    r"marine corps|department of defense|\bdod\b|defense technical information|\bdtic\b|"
    r"\busda\b|dept\.? of agriculture|gov(ernmen)?t\.? print(ing)?\.? off(ice)?|\bg\.?p\.?o\b|peace corps|"
    r"\bfema\b|federal emergency management|civil defense|public health service|"
    r"forest service|national park service|fish and wildlife|geological survey|"
    r"bureau of (mines|reclamation|indian|plant|animal|chemistry|entomology)|"
    r"headquarters,? department|extension service|soil conservation service|"
    r"coast guard|federal civil defense", re.I)
PD_LICENSE = re.compile(r"publicdomain|/zero/", re.I)
EXCLUDE_COLL = re.compile(r"^(inlibrary|printdisabled|lendinglibrary|browserlending|"
                          r"internetarchivebooks_restricted)$", re.I)

log_lines = []


def log(m):
    print(m, flush=True)
    log_lines.append(m)


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def text(v):
    return " ".join(str(x) for x in as_list(v))


def search(terms):
    q = ("(" + " OR ".join(f"title:({t}) OR subject:({t})" for t in terms) + ")"
         " AND mediatype:texts AND NOT access-restricted-item:true")
    fields = "identifier,title,date,year,publisher,creator,collection,licenseurl,downloads,language"
    out, cursor = [], None
    while len(out) < MAX_PER_TOPIC * 4:
        params = {"q": q, "fields": fields, "count": "1000"}
        if cursor:
            params["cursor"] = cursor
        data = get_json("https://archive.org/services/search/v1/scrape?" + urllib.parse.urlencode(params))
        out += data.get("items", [])
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(1)
    return out


def pd_basis(d):
    ident = d.get("identifier", "")
    colls = [c.lower() for c in as_list(d.get("collection"))]
    if any(EXCLUDE_COLL.match(c) for c in colls) or any(c.startswith("sim_") for c in colls) \
            or ident.startswith("sim_"):
        return None, "lending or periodical collection"
    title = text(d.get("title"))
    publisher = text(d.get("publisher"))
    creators = as_list(d.get("creator"))
    if ident.startswith(("DTIC_", "milmanual-")) or GOV.search(publisher) \
            or any(GOV_CREATOR.search(str(c)) for c in creators):
        return "usgov", ""
    if (DESIGNATION.search(title) or GOV_SERIES.search(title)) and not publisher.strip():
        return "usgov", ""
    try:
        year = int(str(d.get("year") or text(d.get("date"))[:4]))
    except ValueError:
        year = None
    if year and year <= 1930 and year != 1900:
        return "pre1931", ""
    return None, "no clear public-domain basis"


def norm_title(t):
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()[:80]


def pick_files(identifier):
    """Pick one PDF (and an EPUB if present) under the size limit."""
    md = get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    files = md.get("files", [])
    pdfs, epubs = [], []
    for f in files:
        name, size = f.get("name", ""), int(f.get("size") or 0)
        if not size or size > FILE_LIMIT or name.startswith("__"):
            continue
        low = name.lower()
        if low.endswith(".pdf"):
            rank = 0 if f.get("source") == "original" else (1 if f.get("format") == "Text PDF" else 2)
            pdfs.append((rank, size, name))
        elif low.endswith(".epub"):
            epubs.append((size, name))
    chosen = []
    if pdfs:
        chosen.append(sorted(pdfs)[0][2])
    if epubs:
        chosen.append(sorted(epubs)[0][1])
    sizes = {f.get("name"): int(f.get("size") or 0) for f in files}
    return [(n, sizes.get(n, 0)) for n in chosen]


def save(manifest, cand_rows):
    with open(os.path.join(CATALOG, "manifest.tsv"), "w", encoding="utf-8") as f:
        f.write("url\tfolder\tidentifier\tsize\tbasis\ttitle\n")
        for r in manifest:
            f.write("\t".join(r) + "\n")
    with open(os.path.join(CATALOG, "candidates.tsv"), "w", encoding="utf-8") as f:
        f.write("folder\tidentifier\ttitle\tyear\tpublisher\tbasis\treason\n")
        for r in cand_rows:
            f.write("\t".join(r) + "\n")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "add", "catalog", "logs"], cwd=ROOT)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
            subprocess.run(["git", "commit", "-q", "-m", "Update library catalog"], cwd=ROOT)
            subprocess.run(["git", "pull", "-q", "--rebase"], cwd=ROOT)
            subprocess.run(["git", "push", "-q"], cwd=ROOT)


def main():
    os.makedirs(CATALOG, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    seen_ids, seen_titles = set(), set()
    cand_rows, manifest = [], []
    for folder, terms in TOPICS:
        hits = search(terms)
        pat = TITLE_RE[folder]

        def rank(d):
            t = text(d.get("title"))
            manual = bool(DESIGNATION.search(t) or GOV_SERIES.search(t) or re.search(r"manual|handbook|guide", t, re.I))
            return (not manual, -int(d.get("downloads") or 0))
        hits.sort(key=rank)
        kept = 0
        for d in hits:
            ident = d.get("identifier", "")
            title = text(d.get("title")) or ident
            if ident in seen_ids:
                continue
            seen_ids.add(ident)
            basis, why = pd_basis(d)
            if basis and not pat.search(title):
                basis, why = None, "title not on topic"
            elif basis and EXCLUDE_TITLE.search(title):
                basis, why = None, "excluded subject"
            lang = text(d.get("language")).lower()
            if basis and lang and not re.search(r"eng|english", lang):
                basis, why = None, f"language {lang}"
            nt = norm_title(title)
            if basis and nt in seen_titles:
                basis, why = None, "duplicate title"
            row = [folder, ident, title.replace("\t", " ")[:200], text(d.get("year") or d.get("date"))[:10],
                   text(d.get("publisher"))[:80].replace("\t", " "), basis or "-", why]
            cand_rows.append(row)
            if not basis or kept >= MAX_PER_TOPIC:
                continue
            try:
                picks = pick_files(ident)
            except Exception as e:
                row[-1] = f"metadata error: {e}"
                continue
            if not picks:
                row[-1] = "no PDF/EPUB under 95 MB"
                continue
            seen_titles.add(nt)
            kept += 1
            for name, size in picks:
                url = f"https://archive.org/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(name)}"
                manifest.append([url, folder, ident, str(size), basis, title.replace("\t", " ")[:200]])
            time.sleep(0.1)
        log(f"{folder}: {len(hits)} hits, {kept} items kept")
        save(manifest, cand_rows)
    save(manifest, cand_rows)
    total = sum(int(r[3]) for r in manifest)
    log(f"Manifest: {len(manifest)} files from {len({r[2] for r in manifest})} items, {total/1e9:.2f} GB")
    with open(os.path.join(LOGS, "catalog-run.md"), "w", encoding="utf-8") as f:
        f.write("# Catalog run\n\n```\n" + "\n".join(log_lines) + "\n```\n")
    save(manifest, cand_rows)


if __name__ == "__main__":
    main()
