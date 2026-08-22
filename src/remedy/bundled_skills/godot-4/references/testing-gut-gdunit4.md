# Testing: GUT and gdUnit4

Both are editor addons under `addons/`; both run headless. Pick one per
project. Check `project.godot` `[editor_plugins]` to see which is enabled.

## GUT (GDScript only)
Install: AssetLib "Gut" or clone into `addons/gut/`; enable the plugin.
Test file `test/unit/test_player.gd`:
```gdscript
extends GutTest

var Player := preload("res://player/player.tscn")

func test_starts_with_full_health() -> void:
    var p := add_child_autofree(Player.instantiate())
    assert_eq(p.health, p.max_health)

func test_takes_damage() -> void:
    var p := add_child_autofree(Player.instantiate())
    watch_signals(p)
    p.take_damage(3)
    assert_eq(p.health, p.max_health - 3)
    assert_signal_emitted(p, "health_changed")
```
Run:
```text
godot --headless --path . -s addons/gut/gut_cmdln.gd -gdir=res://test -ginclude_subdirs -gexit
```
Options: `-gtest=res://test/unit/test_player.gd` (one file),
`-gunit_test_name=test_takes_damage`, `-glog=2`,
`-gjunit_xml_file=build/gut.xml`. Or put settings in `.gutconfig.json` and
run with just `-gexit`. Exit code is non-zero on failures. Assertions:
`assert_eq`, `assert_ne`, `assert_true`, `assert_almost_eq(a, b, eps)`,
`assert_null`, `assert_has(arr, x)`, `assert_signal_emitted`. Doubles:
`double(Script)`, `stub(d, "method").to_return(1)`.

## gdUnit4 (GDScript and C#)
Install: AssetLib "gdUnit4" or clone into `addons/gdUnit4/`; enable plugin.
Test file `test/player_test.gd`:
```gdscript
extends GdUnitTestSuite

func test_damage() -> void:
    var p := auto_free(preload("res://player/player.tscn").instantiate())
    p.take_damage(3)
    assert_int(p.health).is_equal(p.max_health - 3)
```
Run:
```text
godot --headless --path . -s addons/gdUnit4/bin/GdUnitCmdTool.gd --add res://test
godot --headless --path . -s addons/gdUnit4/bin/GdUnitCmdTool.gd --add res://test/player_test.gd --ignoreHeadlessMode
```
`-c` / `--continue` keeps going after failures; reports land in
`reports/report_N/`. Fluent assertions: `assert_str`, `assert_int`,
`assert_float`, `assert_array`, `assert_object`,
`assert_signal(obj).is_emitted("died")`. Scene runner for input/frames:
`var r := scene_runner("res://player/player.tscn");
r.simulate_action_press("jump"); await r.simulate_frames(10)`.

## What to test
- Pure logic (damage maths, inventory, state machines): unit tests, fast.
- Scene boots and required nodes exist: smoke script, not a test framework.
- Feel, timing, fun: playtest; tests cannot judge it.
Keep the suite under ~30 s headless or nobody runs it.

## Wiring into verify
After a change: `godot_check` → smoke → tests (when the change touched
tested code or the owner asked). Report exact counts from the runner
output; never say "tests pass" without the line that says so.
