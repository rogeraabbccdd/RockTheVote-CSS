#!/usr/bin/env python3
"""Sync a Steam Workshop collection into RockTheVote's maplist.txt and the panorama addon's map thumbnails.

The vote panel cannot load images at runtime: the client's custom HUD validator only allows
basic Panel/Label/Button layouts, and panorama CSS only accepts file://, panel:// and s2r://
URLs. So every thumbnail has to be compiled into the workshop addon.

What it does:
  * adds the collection's maps that are missing from maplist.txt as "Name:workshopId"
    (existing names, comments and order are kept; maps no longer in the collection are only
    reported, never removed)
  * downloads each map's workshop preview, crops/resizes it to 128x72 and saves it as
    <addon>/panorama/images/custom_game/rtv_maps/<workshopId>.png
  * writes <addon>/panorama/styles/custom_game/rockthevote_maps.css, which turns the thumbnails
    on: one class per PNG in rtv_maps/, the game's own screenshots for official maps, and a
    placeholder for every other map

To give any map a custom picture, put <workshopId>.png (or <map name>.png for maps without an
id) into rtv_maps/ and run the script again. With --compile the stylesheet and its PNGs are compiled
with CS2's resourcecompiler; then republish the addon.

Requires Pillow (pip install pillow).

Example:
  python tools/sync_workshop_collection.py --collection 3077158527 --maplist maplist.txt \\
      --addon "C:/.../content/csgo_addons/my_addon"
"""

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

API = "https://api.steampowered.com/ISteamRemoteStorage"
BATCH = 100
THUMB_SIZE = (128, 72)  # must match .rtv-vote-img in rockthevote.css
IMAGE_DIR = "panorama/images/custom_game/rtv_maps"
CSS_PATH = "panorama/styles/custom_game/rockthevote_maps.css"

# Row width steps baked into rockthevote.css (CustomHud.WidthSteps); widened by the thumbnail column
WIDTH_STEPS = range(240, 641, 40)
IMAGE_COLUMN = 140  # thumbnail width + gap

# CS2 ships panorama/images/map_icons/screenshots/360p/<map>_png.vtex for these maps
BUILTIN_MAPS = [
    "ar_baggage", "ar_pool_day", "ar_shoots", "ar_shoots_night", "cs_italy", "cs_office", "cs_shelter",
    "de_ancient", "de_ancient_night", "de_anubis", "de_boulder", "de_cache", "de_debris", "de_dust",
    "de_dust2", "de_eldorado", "de_fachwerk", "de_inferno", "de_mirage", "de_nuke", "de_overpass",
    "de_poseidon", "de_train", "de_vertigo",
]
PLACEHOLDER = "s2r://panorama/images/map_icons/screenshots/360p/random_png.vtex"


def post(endpoint, fields):
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(f"{API}/{endpoint}/v1/", data=data, timeout=30) as resp:
        return json.load(resp)["response"]


def collection_items(collection_id, seen=None):
    """Workshop item ids in a collection, in collection order. Nested collections are expanded."""
    seen = seen if seen is not None else set()
    seen.add(collection_id)
    resp = post("GetCollectionDetails", {"collectioncount": 1, "publishedfileids[0]": collection_id})
    details = resp.get("collectiondetails", [{}])[0]
    if details.get("result") != 1:
        raise SystemExit(f"Collection {collection_id} not found or not public (result={details.get('result')}).")

    ids = []
    for child in sorted(details.get("children", []), key=lambda c: c.get("sortorder", 0)):
        cid = child["publishedfileid"]
        if child.get("filetype") == 2:  # nested collection
            if cid not in seen:
                ids += collection_items(cid, seen)
        else:
            ids.append(cid)
    return list(dict.fromkeys(ids))


def item_details(ids):
    result = {}
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        fields = {"itemcount": len(chunk)}
        fields.update({f"publishedfileids[{i}]": fid for i, fid in enumerate(chunk)})
        for item in post("GetPublishedFileDetails", fields).get("publishedfiledetails", []):
            if item.get("result") == 1:
                result[item["publishedfileid"]] = item
    return result


def map_name_from_title(title, workshop_id):
    name = re.sub(r"\s+", "_", title.strip())
    name = re.sub(r"[^A-Za-z0-9_.\-]", "", name).strip("_")
    return name or f"workshop_{workshop_id}"


def download_thumbnail(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "RockTheVote-maplist-sync"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        img = Image.open(io.BytesIO(resp.read()))
        img.seek(0)  # first frame of animated previews
        img = ImageOps.fit(img.convert("RGB"), THUMB_SIZE, Image.LANCZOS)
    img.save(dest, "PNG", optimize=True)


def parse_line(line):
    """(name, id) for a map line, or None for blank lines and comments. A third field is ignored."""
    stripped = line.strip()
    if not stripped or stripped.startswith("//"):
        return None
    parts = stripped.split(":", 2)
    return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""


def format_line(name, workshop_id):
    return f"{name}:{workshop_id}" if workshop_id else name


def image_class(key):
    """Must match CustomHud.MapImageClass in the plugin (key = workshop id, or lower-case map name)."""
    return "rtv-mi-" + "".join(c if (c.isascii() and c.isalnum()) or c in "_-" else "_" for c in key.lower())


def build_css(image_dir):
    out = [
        "/* RockTheVote map thumbnails for the panorama vote panel.",
        "   Generated by tools/sync_workshop_collection.py; rebuild the addon after replacing this file. */",
        "",
        "/* Turn the thumbnail column on; maps without a picture show the placeholder */",
        f'.rtv-vote-img.rtv-img-on {{ visibility: visible; background-image: url("{PLACEHOLDER}"); }}',
        f".rtv-vote-row-label.rtv-label-with-img {{ margin-left: {IMAGE_COLUMN}px; vertical-align: center; }}",
        "",
        "/* Rows widened by the thumbnail column */",
    ]
    out += [f".rtv-vote-row.rtv-w-{w} {{ width: {w + IMAGE_COLUMN}px; }}" for w in WIDTH_STEPS]
    out.append("")

    # Two classes so these beat the placeholder rule above. Addon PNGs must be referenced as
    # file://{images}/...png: that is a strong reference, so compiling the stylesheet also
    # compiles the PNG. An s2r://..._png.vtex reference is weak and the PNG never gets built.
    images_rel = IMAGE_DIR.removeprefix("panorama/images/")
    pictures = {p.stem.lower(): f"file://{{images}}/{images_rel}/{p.name}" for p in sorted(image_dir.glob("*.png"))}
    for name in BUILTIN_MAPS:
        pictures.setdefault(name, f"s2r://panorama/images/map_icons/screenshots/360p/{name}_png.vtex")
    for key, url in pictures.items():
        out.append(f'.rtv-vote-img.{image_class(key)} {{ background-image: url("{url}"); }}')
    return "\n".join(out) + "\n", len(pictures)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collection", required=True, help="Steam Workshop collection id")
    ap.add_argument("--maplist", default="maplist.txt", help="path to maplist.txt (default: maplist.txt)")
    ap.add_argument("--addon", required=True, help="the workshop addon's content folder"
                                                   " (the one that contains panorama/layout/custom_game/rockthevote.xml)")
    ap.add_argument("--prune", action="store_true", help="remove maplist lines whose workshop item was removed or made private")
    ap.add_argument("--remove-uncollected", action="store_true", help="remove workshop maps that are not in the collection"
                                                                  " (maps without a workshop id are kept)")
    ap.add_argument("--compile", action="store_true", help="compile rockthevote_maps.css and its PNGs with CS2's"
                                                     " resourcecompiler (needs the addon inside <CS2>/content/csgo_addons)")
    ap.add_argument("--force", action="store_true", help="re-download thumbnails that already exist")
    ap.add_argument("--dry-run", action="store_true", help="show changes without writing anything")
    args = ap.parse_args()

    if not args.addon.strip():
        raise SystemExit("--addon is empty; pass the addon content folder that holds rockthevote.xml.")
    addon = Path(args.addon)
    if not (addon / "panorama").is_dir():
        raise SystemExit(f"{addon} has no panorama folder; pass the addon content folder that holds rockthevote.xml.")

    print(f"Reading collection {args.collection} ...")
    ids = collection_items(args.collection)
    details = item_details(ids)
    print(f"  {len(ids)} items, {len(details)} available")
    for fid in ids:
        if fid not in details:
            print(f"  ! {fid} is unavailable (removed or private), skipped")

    # Thumbnails
    image_dir = addon / IMAGE_DIR
    if not args.dry_run:
        image_dir.mkdir(parents=True, exist_ok=True)
    downloaded = failed = 0
    for fid, item in details.items():
        dest = image_dir / f"{fid}.png"
        preview = item.get("preview_url")
        if not preview or (dest.exists() and not args.force):
            continue
        if args.dry_run:
            downloaded += 1
            continue
        try:
            download_thumbnail(preview, dest)
            downloaded += 1
        except Exception as ex:  # keep going; one bad preview should not stop the sync
            failed += 1
            print(f"  ! thumbnail for {fid} ({item.get('title')}) failed: {ex}")
    print(f"Thumbnails: {downloaded} downloaded, {failed} failed -> {image_dir.resolve()}")

    # maplist.txt
    maplist = Path(args.maplist)
    raw = maplist.read_bytes().decode("utf-8-sig") if maplist.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    listed_ids = {p[1] for p in map(parse_line, lines) if p and p[1]}
    # Listed maps outside the collection need their own lookup to tell "removed" from "just not collected"
    available = set(details) | set(item_details(sorted(listed_ids - set(details))))
    collection = set(ids)
    listed = set()
    stripped = 0
    pruned = []
    new_lines = []
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            new_lines.append(line)
            continue
        name, fid = parsed
        if fid and fid not in collection and args.remove_uncollected:
            pruned.append(f"{name}:{fid}")
            print(f"  - {name}:{fid} is not in the collection, removed")
            continue
        if fid and fid not in available:
            if args.prune:
                pruned.append(f"{name}:{fid}")
                print(f"  - {name}:{fid} is no longer on the workshop, removed")
                continue
            print(f"  ! {name}:{fid} is no longer on the workshop (use --prune to remove it)")
        if fid:
            listed.add(fid)
        new_line = format_line(name, fid)
        if new_line == line.strip():
            new_lines.append(line)
        else:
            new_lines.append(new_line)
            stripped += 1  # old "name:id:image" line

    added = [fid for fid in ids if fid in details and fid not in listed]
    for fid in added:
        name = map_name_from_title(details[fid].get("title", ""), fid)
        new_lines.append(format_line(name, fid))
        print(f"  + {name}:{fid}")

    for fid in sorted((listed & available) - set(ids)):
        print(f"  ? {fid} is in {maplist.name} but not in the collection (use --remove-uncollected to remove it)")

    print(f"{maplist.name}: {len(added)} added, {len(pruned)} removed, {stripped} old image columns removed")
    if args.dry_run:
        print("Dry run, nothing written.")
        return
    if added or pruned or stripped:
        if maplist.exists():
            shutil.copyfile(maplist, maplist.with_name(maplist.name + ".bak"))
        maplist.write_bytes((newline.join(new_lines) + newline).encode("utf-8"))

    css, count = build_css(image_dir)
    css_file = addon / CSS_PATH
    css_file.parent.mkdir(parents=True, exist_ok=True)
    css_file.write_text(css, encoding="utf-8", newline="\n")
    print(f"Wrote {count} map pictures to {css_file.resolve()}")

    if args.compile:
        compile_css(addon, css_file)


def compile_css(addon, css_file):
    """Compile the stylesheet; its file://{images} PNGs are strong references and get compiled with it."""
    # <CS2>/content/csgo_addons/<addon> -> <CS2>/game/bin/win64/resourcecompiler.exe
    cs2_root = addon.resolve().parents[2]
    compiler = cs2_root / "game" / "bin" / "win64" / "resourcecompiler.exe"
    if not compiler.is_file():
        raise SystemExit(f"resourcecompiler not found at {compiler}; is the addon inside <CS2>/content/csgo_addons?")

    print("Compiling ...")
    result = subprocess.run([str(compiler), "-nop4", "-i", str(css_file.resolve())],
                            cwd=compiler.parent, capture_output=True, text=True, errors="replace")
    summary = [line.strip() for line in result.stdout.splitlines() if "compiled," in line]
    errors = [line.strip() for line in result.stdout.splitlines() if "error" in line.lower() and "compiled," not in line]
    for line in errors[:20]:
        print(f"  {line}")
    print(summary[-1] if summary else "  (no summary from resourcecompiler)")
    if result.returncode != 0:
        raise SystemExit(f"resourcecompiler exited with code {result.returncode}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
