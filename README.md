# DOOM 3 BFG Resource Editor

<img width="1920" height="1080" alt="Screenshot (1065)" src="https://github.com/user-attachments/assets/ac761621-aca8-45ca-a13f-bf31bad830cd" />


A standalone **Python + Tkinter** GUI for browsing and editing DOOM 3 BFG
Edition `.resources` container files — including the **Xbox 360** build's
mixed‑endian variant (your `_common.resources`) as well as the PC build.

It was built directly against id Software's now‑open‑source format code
(`neo/framework/File_Resource.cpp` / `File_Resource.h`) and verified to
round‑trip your archive **byte‑for‑byte identically** (MD5 matched) before any
edit, so saved files stay valid for the engine.

## What it does

- **Browse** every file inside a `.resources` archive (yours has 2,105).
- **Filter** the list instantly by filename.
- **Edit text decls in place** — `.script`, `.def`, `.gui`, `.mtr`,
  `.sndshd`, `.skin`, `.fx`, `.pda`, `.prt`, `.af`, etc. (1,081 of the 2,105
  entries in `_common.resources` are readable text). Edit in the pane, click
  **Save Edit Into Archive**, then **Save**.
- **Hex preview** for binary entries (`.bimage`, `.bmd5anim`, `.cgb`, …).
- **Extract** one file or **Extract All** to a folder tree.
- **Replace** any entry's bytes from an external file.
- **Add** and **Delete** entries.
- **Save / Save As**, rebuilding the archive with correct offsets and the
  source file's original byte order.

## Running it

Tkinter ships with the official Python installers on Windows and macOS, so no
third‑party packages are required.

```bash
python resource_editor_gui.py
# or open a file directly:
python resource_editor_gui.py _common.resources
```

On Linux you may need `sudo apt install python3-tk` first.

> ⚠️ Always keep a backup of your original `.resources` file before editing.

## Files

| File                     | Purpose                                                        |
|--------------------------|---------------------------------------------------------------|
| `resource_editor_gui.py` | The Tkinter GUI application.                                   |
| `resource_archive.py`    | The format engine (no GUI dependency; importable / scriptable).|

You can also use the engine on its own:

```python
from resource_archive import ResourceArchive
arc = ResourceArchive.load("_common.resources")
e = arc.find("script/doom_main.script")
arc.replace_data(e.name, e.data.replace(b"foo", b"bar"))
arc.save("_common.resources")
```

## The `.resources` format (as implemented)

```
HEADER (12 bytes)
  magic        uint32   0xD000000D
  tableOffset  int32    offset of the lookup table
  tableLength  int32    byte length of the lookup table

BODY
  raw file blobs, concatenated

TABLE (at tableOffset)
  numFiles     int32
  per file:
    nameLength int32    length of filename
    name       bytes
    offset     int32    blob offset in the body
    length     int32    blob length
```

**Endianness.** The PC build writes everything big‑endian. The Xbox 360 build
writes the header ints and each entry's `offset`/`length` big‑endian, but the
per‑name `nameLength` prefix little‑endian. The engine auto‑detects both on
load and preserves them on save, so each platform's files round‑trip exactly.

Because edits change blob sizes, **Save** fully rebuilds the file — rewriting
every blob sequentially, recomputing all offsets, and writing a fresh table —
which is exactly what the engine's `WriteResourceFile` / `UpdateResourceFile`
do.
