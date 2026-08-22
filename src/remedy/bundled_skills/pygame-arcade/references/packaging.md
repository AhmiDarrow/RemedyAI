# Packaging

## pyinstaller (most common)

```
pip install pyinstaller
pyinstaller --onefile --windowed --name Game --add-data "assets;assets" main.py
```

- `--add-data SRC;DEST` on Windows, `SRC:DEST` on macOS/Linux.
- `--windowed` hides the console (drop it while debugging so tracebacks
  show; or log to a file).
- `--onefile` unpacks to a temp dir at start (slower launch); `--onedir`
  starts faster and is what itch.io/Steam expect zipped.
- `--icon game.ico` for the exe icon.
- Output: `dist/Game.exe` (or `dist/Game/`). Spec file `Game.spec` can be
  edited and rebuilt with `pyinstaller Game.spec`.
- Unsure about a flag: `pyinstaller --help`.

## Asset paths at runtime

Bundled files live under `sys._MEIPASS` for onefile builds. One helper,
used everywhere assets are loaded:

```python
import sys
from pathlib import Path

def asset_path(rel: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / rel)

img = pygame.image.load(asset_path("assets/hero.png"))
```

Keep `__file__`-relative paths in dev so the same code works unbundled.

## pygame specifics

pygame ships pyinstaller hooks; usually nothing extra. If fonts or mixer
fail in the exe, add `--collect-all pygame`. Silence the banner with
`PYGAME_HIDE_SUPPORT_PROMPT=1` set before import.

## arcade specifics

```
pyinstaller --onefile --windowed --collect-all arcade --collect-all pyglet --add-data "assets;assets" main.py
```

arcade's `:resources:` paths are inside the package; `--collect-all arcade`
keeps them. Pymunk needs `--collect-all pymunk` when used.

## Verify the build

Run `dist/Game.exe` once via `bash_exec` (auto-backgrounded) and take a
screenshot, or `game_playtest(command="dist/Game.exe", seconds=10)`.
A build that "succeeds" but opens and closes instantly is a missing-asset
error — rebuild without `--windowed` to read it.

## Alternatives

- `nuitka --standalone --onefile main.py` — compiles to C; smaller/faster
  exe, longer build; antivirus false positives less common.
- `cx_Freeze` — config in `setup.py`; fine for onedir.
- Web: `pygbag` turns a pygame(-ce) project into WebAssembly for itch.io
  (`pip install pygbag; pygbag .`). Requires `async` main loop — see
  pygbag docs; not a drop-in.

## Shipping to itch.io

Zip the `dist/Game/` folder (onedir) so the exe sits at the zip root, mark
it as a Windows executable on the project page. `butler push Game.zip
user/game:windows` if the owner has butler.

## Antivirus

Fresh pyinstaller exes trip SmartScreen/Defender heuristics. Tell the owner
this is expected for unsigned builds; code signing is the real fix.
