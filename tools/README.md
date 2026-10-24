# sync_workshop_collection.py

Syncs a Steam Workshop collection into `maplist.txt` and builds the map thumbnails for the Panorama End of Map Vote panel (`EndOfMapVote.MenuType` = `panorama`).

## Why the thumbnails live in the addon

The panel cannot load images at runtime:

- The client's custom HUD validator only allows `Panel`, `Label` and `Button` with basic attributes (`id`, `class`, `text`, `hittest`). There is no `Image` panel and no `html` attribute.
- Panorama CSS `url()` only accepts `file://`, `panel://` and `s2r://`. `http(s)` URLs fail to compile.

So the plugin gives every map row a class (`.rtv-mi-<workshopId>`, or `.rtv-mi-<map name>` for maps without an id), and the addon's `panorama/styles/custom_game/rockthevote_maps.css` maps each class to a compiled image. This script generates that stylesheet and the images.

## Requirements

- Python 3.10 or newer
- Pillow: `pip install pillow`
- CS2 Workshop Tools on Windows. The addon has to be in `<CS2>/content/csgo_addons/<addon>`, and `--compile` uses `<CS2>/game/bin/win64/resourcecompiler.exe`.
- The addon already contains `panorama/layout/custom_game/rockthevote.xml` and `panorama/styles/custom_game/rockthevote.css` from this repo (revision 5 or newer).
- The workshop collection must be public.

## Quick start

Sync the collection, remove maps that are gone from the workshop, and compile the thumbnails:

```
python tools/sync_workshop_collection.py --collection <collection id> --maplist maplist.txt --addon "<CS2>/content/csgo_addons/<addon>" --prune --compile
```

`<CS2>` is the game's install folder, e.g. `C:/Program Files (x86)/Steam/steamapps/common/Counter-Strike Global Offensive`.

Preview the changes without writing anything:

```
python tools/sync_workshop_collection.py --collection <collection id> --maplist maplist.txt --addon "<CS2>/content/csgo_addons/<addon>" --prune --dry-run
```

## Options

| Option | Description |
| --- | --- |
| `--collection` | Workshop collection id (required) |
| `--maplist` | Path to `maplist.txt`. Default: `maplist.txt` in the current folder |
| `--addon` | The addon's content folder, the one that holds `panorama/layout/custom_game/rockthevote.xml` (required) |
| `--prune` | Remove maplist lines whose workshop item was removed or made private |
| `--remove-uncollected` | Remove workshop maps that are not in the collection. Maps without a workshop id (official maps) are kept |
| `--compile` | After writing the stylesheet, compile it and its images with `resourcecompiler.exe` |
| `--force` | Download thumbnails again even if they already exist |
| `--dry-run` | Show what would change without writing anything |

## What it does

1. **Reads the collection.** Nested collections are expanded. Items that were removed or made private are listed and skipped.
2. **Downloads thumbnails.** Each map's workshop preview is cropped to 128x72 and saved as `<addon>/panorama/images/custom_game/rtv_maps/<workshopId>.png`. Existing files are not downloaded again.
3. **Updates `maplist.txt`.**
   - Maps in the collection but not in the maplist are appended at the end. The name comes from the workshop title, e.g. `Night Motel` becomes `Night_Motel`.
   - Existing names, comments and order are kept. You can rename maps; later syncs will not overwrite your names.
   - Maps that are not in the collection but still exist on the workshop are only reported (lines starting with `?`), unless you pass `--remove-uncollected`.
   - Before any change, the original file is copied to `maplist.txt.bak`.
4. **Writes `<addon>/panorama/styles/custom_game/rockthevote_maps.css`.**
   - Turns the thumbnail column on and widens each row by 140px.
   - One class for each PNG in `rtv_maps/`.
   - Official maps (`de_dust2`, `cs_office` and 22 others) use the screenshots that ship with CS2.
   - Every other map shows the game's "random map" placeholder.
5. **`--compile`** compiles the stylesheet. New or changed PNGs are compiled to `.vtex_c` with it. The last line should read `OK: N compiled, 0 failed`.

## Updating maps

1. Add maps to, or remove maps from, the workshop collection.
2. Run the quick start command.
3. Publish the addon with the Workshop Tools.
4. Copy `maplist.txt` to the server (`addons/counterstrikesharp/plugins/RockTheVote/`) and run `!reloadmaps`.

New maps can be voted for before the addon is updated. They show the placeholder until then.

## Custom thumbnails

Put a 128x72 PNG in `<addon>/panorama/images/custom_game/rtv_maps/` and run the script again with `--compile`.

- Workshop maps: name the file after the workshop id, e.g. `3075706807.png`. You can overwrite the one the script downloaded; later syncs will not replace it unless you pass `--force`.
- Official maps, or maps without a workshop id: name the file after the map name in lower case, e.g. `de_dust2.png`. It replaces the built-in screenshot.

If you only replace an image's content and keep the file name, the stylesheet does not change. If the compile reports `0 compiled` and the game still shows the old image, compile manually with `-f` (see below).

## Compiling manually

In PowerShell:

```powershell
& "<CS2>\game\bin\win64\resourcecompiler.exe" -nop4 -i "<CS2>\content\csgo_addons\<addon>\panorama\styles\custom_game\rockthevote_maps.css"
```

- Add `-f` to force a full recompile.
- After changing `rockthevote.xml`, compile `...\panorama\layout\custom_game\rockthevote.xml` instead. It compiles the layout, both stylesheets and all thumbnails.

## Cleanup

`--prune` only edits the maplist. It does not delete thumbnails. The PNG and `.vtex_c` of a removed map stay in the addon. They cause no errors, but they make the addon bigger. To remove them, delete the files with that workshop id from:

- `<CS2>/content/csgo_addons/<addon>/panorama/images/custom_game/rtv_maps/`
- `<CS2>/game/csgo_addons/<addon>/panorama/images/custom_game/rtv_maps/`

## Pitfalls

- **Images inside the addon must be referenced as `file://{images}/...png`.** That is a strong reference, so compiling the stylesheet also compiles the PNG. An `s2r://..._png.vtex` reference is weak, and the PNG never gets compiled. The script already does this.
- **Do not copy the repo's `panorama/styles/custom_game/rockthevote_maps.css` into your addon.** It is an empty stock file for servers without thumbnails. Copying it turns all thumbnails off.
- **Keep the plugin and the addon in sync.** The thumbnail classes need a plugin build that includes map thumbnails and addon revision 5. With an older plugin, the panel may not appear at all.
- **`--addon` cannot be empty.** An empty path would mean the current folder, so the script refuses to run.
