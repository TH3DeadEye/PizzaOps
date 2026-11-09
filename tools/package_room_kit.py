"""Build a self-contained DIY sprite kit, searchable catalog and editable example."""

import argparse
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import zipfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from tools.validate_assets import validate
from viewer.assets import AssetPack

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "pizzaops"
NAME = "PizzaOps_Build_Your_Own_Room_Kit"


def make_layouts():
    blank = {"schema_version": 2, "id": "my_room", "title": "Your room", "size": [8, 8],
             "tile_size": [64, 32], "origin": [480, 160], "characters": [],
             "floors": [{"sprite": "floor_wood", "position": [x, y]} for x in range(8) for y in range(8)],
             "objects": [{"sprite": f"wall_cream_{side}", "position": [0, n] if side == "nw" else [n, 0], "layer": "back"}
                         for side in ("nw", "ne") for n in range(8)]}
    folder = ROOT / "examples"
    folder.mkdir(exist_ok=True)
    # These are editable templates. Preserve any user edits on later packaging runs.
    if not (folder / "empty_room.json").exists():
        (folder / "empty_room.json").write_text(json.dumps(blank, indent=2) + "\n")
    furnished = json.loads(json.dumps(blank))
    placements = [("communal_table", 3, 3), ("stool", 3, 4), ("stool", 4, 3),
                  ("bench", 5, 1), ("floor_lamp", 6, 1), ("plant", 1, 1),
                  ("service_counter", 1, 5), ("menu_stand", 1, 7), ("olive_tree", 6, 6)]
    furnished["objects"].extend({"sprite": name, "position": [x, y]} for name, x, y in placements)
    furnished["objects"].extend([
        {"sprite": "pizza_pepperoni", "position": [3, 3], "elevation": 36, "depth_bias": 0.02},
        {"sprite": "register", "position": [1, 5], "elevation": 35, "depth_bias": 0.02},
        {"sprite": "window", "position": [0, 3], "elevation": 24, "layer": "back"},
        {"sprite": "wall_clock", "position": [2, 0], "elevation": 36, "layer": "back"},
        {"sprite": "rug", "position": [5, 6], "layer": "ground"},
    ])
    if not (folder / "my_room.json").exists():
        (folder / "my_room.json").write_text(json.dumps(furnished, indent=2) + "\n")


def make_catalog(pack):
    rows = []
    for name, entry in pack.sprites.items():
        rows.append({"id": name, "category": entry["category"], "png": f"assets/pizzaops/{entry['file']}",
                     "width": entry["size"][0], "height": entry["size"][1],
                     "anchor_x": entry["anchor"][0], "anchor_y": entry["anchor"][1],
                     "footprint_x": entry["footprint"][0], "footprint_y": entry["footprint"][1]})
    with (ROOT / "asset_catalog.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    cards = []
    for row in rows:
        name, category = row["id"], row["category"]
        frame = category == "characters" and not name.endswith("idle_se_00")
        cards.append(f'<article data-name="{name}" data-category="{category}" data-frame="{int(frame)}">'
                     f'<a class="art" href="{row["png"]}" download><img loading="lazy" src="{row["png"]}" alt="{name}"></a>'
                     f'<h2>{name}</h2><p>{category} · {row["width"]} × {row["height"]} px</p>'
                     f'<p>Anchor {row["anchor_x"]}, {row["anchor_y"]} · Footprint {row["footprint_x"]} × {row["footprint_y"]}</p>'
                     f'<a href="{row["png"]}" download>PNG</a> <button data-id="{name}">Copy ID</button></article>')
    options = ''.join(f'<option>{html.escape(c)}</option>' for c in sorted({r["category"] for r in rows}))
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PizzaOps / Build your own room</title><style>
*{box-sizing:border-box}body{margin:0;background:#302c32;color:#f3dfb6;font:16px system-ui,sans-serif}header{padding:28px 4%;background:#24272c}h1{margin:0 0 8px;font-size:30px}p{line-height:1.5}header p{color:#c8ad8d;max-width:850px}a{color:#bad9bf}nav{position:sticky;top:0;z-index:1;display:flex;gap:16px;align-items:center;flex-wrap:wrap;background:#24272c;padding:16px 4%;border-bottom:1px solid #51464b}input,select,button{padding:9px;border:1px solid #817165;border-radius:5px;background:#3a3438;color:#f3dfb6}input[type=search]{width:280px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;padding:24px 4%}article{padding:12px;background:#40383e;border-radius:8px;min-width:0}article[hidden]{display:none}.art{display:flex;height:180px;justify-content:center;align-items:center;background:repeating-conic-gradient(#59505a 0 25%,#4a414c 0 50%) 0/20px 20px;border-radius:5px}.art img{image-rendering:pixelated;object-fit:contain;max-width:100%;max-height:100%}h2{font-size:15px;overflow-wrap:anywhere;margin-bottom:4px}article p{font-size:12px;color:#c8ad8d;margin:3px 0}article button{margin:8px 0 0 16px;cursor:pointer}#count{color:#c8ad8d;font-size:14px}footer{padding:24px 4%;color:#c8ad8d}</style>
<header><h1>PIZZAOPS / BUILD YOUR OWN ROOM</h1><p>Every tile, furnishing, prop and character is a separate transparent PNG. Click a picture to save it, or copy its sprite ID into your layout. Checkerboards are preview backgrounds only.</p><a href="START_HERE.md">Instructions</a> · <a href="asset_catalog.csv">Asset list CSV</a> · <a href="assets/pizzaops/manifest.json">Metadata JSON</a> · <a href="assets/pizzaops/strips/">Animation strips folder</a></header>
<nav><input id="search" type="search" placeholder="Search: table, oven, chef…" aria-label="Search sprites"><select id="category" aria-label="Category"><option value="">All categories</option>OPTIONS</select><label><input id="frames" type="checkbox"> Show every animation frame</label><span id="count"></span></nav><main>CARDS</main><footer>64 × 32 floor grid · Original modular artwork · See ASSET_LICENSE.md for permissions.</footer>
<script>const cards=[...document.querySelectorAll('article')],search=document.querySelector('#search'),category=document.querySelector('#category'),frames=document.querySelector('#frames');function filter(){let n=0;for(const c of cards){c.hidden=!(c.dataset.name.includes(search.value.trim().toLowerCase())&&(!category.value||c.dataset.category===category.value)&&(frames.checked||c.dataset.frame==='0'));if(!c.hidden)n++}document.querySelector('#count').textContent=n+' sprites shown / '+cards.length+' total'}for(const el of [search,category,frames])el.addEventListener('input',filter);document.querySelectorAll('button').forEach(b=>b.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(b.dataset.id);b.textContent='Copied';setTimeout(()=>b.textContent='Copy ID',1200)}catch{window.prompt('Copy this sprite ID',b.dataset.id)}}));filter();</script></html>'''
    (ROOT / "catalog.html").write_text(document.replace("OPTIONS", options).replace("CARDS", ''.join(cards)))
    visible = [r["id"] for r in rows if r["category"] != "characters" or r["id"].endswith("idle_se_00")]
    draw_catalog(pack, visible, ASSETS / "previews" / "build_your_own_catalog.png", columns=8)
    draw_catalog(pack, ["bench", "stool", "communal_table"], ASSETS / "previews" / "new_furniture.png", columns=3, cell=(240, 248), max_scale=3)


def draw_catalog(pack, names, path, *, columns, cell=(160, 180), max_scale=2):
    width, height = cell
    canvas = pygame.Surface((columns * width, ((len(names) + columns - 1) // columns) * height + 54))
    canvas.fill("#302c32")
    font, small = pygame.font.Font(None, 17), pygame.font.Font(None, 15)
    canvas.blit(pygame.font.Font(None, 28).render("PIZZAOPS / INDIVIDUAL SPRITE CATALOG", True, "#f3dfb6"), (16, 17))
    for index, name in enumerate(names):
        x, y = index % columns * width, 54 + index // columns * height
        pygame.draw.rect(canvas, "#453c45", (x + 4, y + 4, width - 8, height - 8), border_radius=4)
        image = pack.image(name)
        crop = image.subsurface(image.get_bounding_rect())
        factor = max(1, min(max_scale, (width - 20) // crop.get_width(), (height - 54) // crop.get_height()))
        scaled = pygame.transform.scale(crop, (crop.get_width() * factor, crop.get_height() * factor))
        canvas.blit(scaled, (x + (width - scaled.get_width()) // 2, y + height - 48 - scaled.get_height()))
        canvas.blit(font.render(name, True, "#f3dfb6"), (x + 10, y + height - 38))
        entry = pack.sprites[name]
        canvas.blit(small.render(f'{entry["size"][0]}x{entry["size"][1]} PNG / alpha', True, "#c8ad8d"), (x + 10, y + height - 21))
    pygame.image.save(canvas, path)


def package(destination):
    destination.mkdir(parents=True, exist_ok=True)
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    make_layouts()
    make_catalog(AssetPack())
    report = validate()
    (ROOT / "ROOM_KIT_VALIDATION.json").write_text(json.dumps(report, indent=2) + "\n")
    (ASSETS / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    (ROOT / "START_HERE.md").write_text((ROOT / "ROOM_KIT.md").read_text())
    files = [p for p in ASSETS.rglob("*") if p.is_file() and p.suffix != ".pyc" and p.name != ".DS_Store"]
    files += [ROOT / name for name in (
        "START_HERE.md", "ROOM_KIT.md", "ROOM_KIT_VALIDATION.json", "catalog.html", "asset_catalog.csv",
        "ASSET_LICENSE.md", "CODE_LICENSE.md", "requirements.txt",
        "viewer/assets.py", "viewer/geometry.py", "viewer/layout.py", "viewer/scene.py", "viewer/asset_demo.py",
        "examples/furnish_room.py", "examples/empty_room.json", "examples/my_room.json",
        "tools/build_assets.py", "tools/build_environment.py", "tools/build_rooms.py", "tools/validate_assets.py", "tools/package_room_kit.py",
    )]
    archive = destination / f"{NAME}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(files):
            bundle.write(path, f"{NAME}/{path.relative_to(ROOT).as_posix()}")
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert len(bundle.namelist()) == len(set(bundle.namelist()))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".sha256").write_text(f"{digest}  {archive.name}\n")
    pygame.quit()
    print(json.dumps({"archive": str(archive), "sprites": report["sprites"], "animations": report["animations"], "atlases": report["atlases"], "bytes": archive.stat().st_size}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    package(parser.parse_args().output)
