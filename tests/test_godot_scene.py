"""Offline Godot scene / GDScript sanity checks (core.godot_scene)."""
from __future__ import annotations

from pathlib import Path

from remedy.core.godot_scene import check_gdscript_text, check_scene, parse_scene

SCENE = '''[gd_scene load_steps=3 format=3 uid="uid://abc"]

[ext_resource type="Script" uid="uid://s1" path="res://player.gd" id="1_p"]
[ext_resource type="Texture2D" path="res://art/hero.png" id="2_t"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_1"]
size = Vector2(16, 16)

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1_p")

[node name="Sprite" type="Sprite2D" parent="."]
texture = ExtResource("2_t")

[node name="Shape" type="CollisionShape2D" parent="."]
shape = SubResource("RectangleShape2D_1")
'''


def _project(tmp_path: Path) -> Path:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "player.gd").write_text("extends CharacterBody2D\n", encoding="utf-8")
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "hero.png").write_bytes(b"\x89PNG")
    return tmp_path


def test_parse_sections_and_attrs():
    secs = parse_scene(SCENE)
    assert [s.kind for s in secs][:3] == ["gd_scene", "ext_resource", "ext_resource"]
    assert secs[1].attrs["path"] == "res://player.gd"
    assert secs[1].attrs["id"] == "1_p"
    node = next(s for s in secs if s.kind == "node" and s.attrs["name"] == "Sprite")
    assert node.attrs["parent"] == "."
    assert node.body == ['texture = ExtResource("2_t")']


def test_a_consistent_scene_is_ok(tmp_path: Path):
    root = _project(tmp_path)
    p = root / "player.tscn"
    p.write_text(SCENE, encoding="utf-8")
    res = check_scene(p)
    assert res["ok"], res
    assert res["engine"] == "tscn-parse"
    # The texture row has no uid — a warning, not an error.
    assert any("no uid" in w for w in res["warnings"])


def test_missing_resource_and_undeclared_reference_are_errors(tmp_path: Path):
    root = _project(tmp_path)
    (root / "art" / "hero.png").unlink()
    broken = SCENE.replace('ExtResource("1_p")', 'ExtResource("9_x")')
    p = root / "player.tscn"
    p.write_text(broken, encoding="utf-8")
    res = check_scene(p)
    assert not res["ok"]
    assert "missing resource res://art/hero.png" in res["error"]
    assert "ExtResource('9_x') is not declared" in res["error"]


def test_duplicate_ids_and_missing_root_node(tmp_path: Path):
    root = _project(tmp_path)
    text = (
        '[gd_scene format=3]\n'
        '[ext_resource type="Script" path="res://player.gd" id="1"]\n'
        '[ext_resource type="Script" path="res://player.gd" id="1"]\n'
        '[node name="Child" type="Node2D" parent="."]\n'
    )
    res = check_scene(root / "x.tscn", text)
    assert not res["ok"]
    assert "duplicate ext_resource id '1'" in res["error"]
    assert "no root node" in res["error"]


def test_a_resource_file_outside_any_project_skips_path_checks(tmp_path: Path):
    text = '[gd_resource type="Resource" format=3]\n[resource]\nvalue = 1\n'
    res = check_scene(tmp_path / "thing.tres", text)
    assert res["ok"]


def test_garbage_is_not_a_scene(tmp_path: Path):
    res = check_scene(tmp_path / "x.tscn", "hello\n")
    assert not res["ok"] and "header" in res["error"]


# --- GDScript fallback ----------------------------------------------------------


def test_clean_gdscript_passes():
    ok, err = check_gdscript_text(
        "extends CharacterBody2D\n\n"
        "@export var speed := 200.0\n\n"
        "func _physics_process(delta: float) -> void:\n"
        "\tvar dir := Input.get_axis(\"left\", \"right\")\n"
        "\tvelocity.x = dir * speed  # comment with ( bracket\n"
        "\tif dir != 0 and is_on_floor():\n"
        "\t\tmove_and_slide()\n"
        "\tmatch state:\n"
        "\t\t\"idle\":\n"
        "\t\t\tpass\n"
        "\tvar s := \"it's fine\"\n"
    )
    assert ok, err


def test_missing_colon_and_unbalanced_bracket_are_caught():
    ok, err = check_gdscript_text("func _ready()\n\tprint((1 + 2)\n")
    assert not ok
    assert "without ':'" in err
    assert "unclosed '('" in err


def test_mixed_indentation_is_caught():
    ok, err = check_gdscript_text("func a():\n\tpass\n\nfunc b():\n    pass\n")
    assert not ok and "indentation" in err


def test_multiline_call_is_not_a_missing_colon():
    ok, err = check_gdscript_text(
        "func _ready():\n"
        "\tvar t := Tween.new(\n"
        "\t\t1.0,\n"
        "\t)\n"
        "\tif (a\n"
        "\t\tand b):\n"
        "\t\tpass\n"
    )
    assert ok, err
