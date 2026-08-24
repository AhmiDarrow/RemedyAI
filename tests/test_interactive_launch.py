"""GUI/game launch detection + compile-only verify rewrite."""

from __future__ import annotations

from pathlib import Path

from remedy.core.interactive_launch import (
    command_looks_like_gui_launch,
    compile_only_verify_command,
    path_looks_like_gui,
    source_looks_like_gui,
    write_set_looks_like_gui,
)


def test_source_looks_like_gui_pygame_and_sdl():
    assert source_looks_like_gui("import pygame\npygame.init()")
    assert source_looks_like_gui("#include <SDL.h>\nint main(){SDL_Init(0);}")
    assert source_looks_like_gui("int WINAPI WinMain(HINSTANCE h, HINSTANCE p, LPSTR c, int n)")
    assert not source_looks_like_gui('#include <stdio.h>\nint main(){printf("hi");return 0;}')
    assert not source_looks_like_gui("def add(a,b):\n    return a+b\n")


def test_path_and_write_set_gui(tmp_path: Path):
    game = tmp_path / "game.py"
    game.write_text("import pygame\nprint('hi')\n", encoding="utf-8")
    hello = tmp_path / "hello.c"
    hello.write_text("int main(){return 0;}\n", encoding="utf-8")
    assert path_looks_like_gui(game)
    assert not path_looks_like_gui(hello)
    assert write_set_looks_like_gui([str(game)], cwd=tmp_path)
    assert not write_set_looks_like_gui([str(hello)], cwd=tmp_path)


def test_command_gui_python_and_exe(tmp_path: Path):
    (tmp_path / "game.py").write_text("import pygame\n", encoding="utf-8")
    assert command_looks_like_gui_launch("python game.py", tmp_path)
    assert command_looks_like_gui_launch("pygame.display.set_mode((4,4))")
    # hello world compile+run is NOT auto-background
    (tmp_path / "hello.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    assert not command_looks_like_gui_launch(
        "gcc -o hello.exe hello.c && hello.exe", tmp_path
    )
    # bare game exe (no compiler in the command) is treated as a launch
    assert command_looks_like_gui_launch(".\\snake.exe", tmp_path)


def test_compile_only_drops_run_half():
    assert (
        compile_only_verify_command("gcc -o hello.exe hello.c && hello.exe")
        == "gcc -o hello.exe hello.c"
    )
    assert (
        compile_only_verify_command("python game.py")
        == "python -m py_compile game.py"
    )
    # pytest stays pytest
    assert compile_only_verify_command("pytest -q") == "pytest -q"


# --- engines: headless verification must stay synchronous --------------------


def test_headless_engine_commands_are_not_gui_launches(tmp_path: Path):
    # These print and exit; backgrounding them returns nothing to the agent.
    for cmd in (
        r".\Godot_v4.3-stable_win64_console.exe --headless --path . --quit-after 1",
        r".\Godot_v4.3_console.exe --headless --path . -s tools/smoke_boot.gd",
        "godot4 --headless --check-only -s player.gd",
        r".\Godot.exe --headless --export-release Windows build/game.exe",
        r".\Godot.exe --headless --import --path .",
        "godot --version",
        "luac -p main.lua",
        "cargo check",
    ):
        assert not command_looks_like_gui_launch(cmd, tmp_path), cmd


def test_windowed_engine_launches_are_backgrounded(tmp_path: Path):
    assert command_looks_like_gui_launch(r".\Godot_v4.3.exe --path .", tmp_path)
    assert command_looks_like_gui_launch("love .", tmp_path)
    assert command_looks_like_gui_launch("lovec.exe .", tmp_path)


def test_explorer_folder_is_a_gui_launch():
    assert command_looks_like_gui_launch(
        r'explorer C:\Users\Administrator\Desktop\example-folder'
    )
    assert command_looks_like_gui_launch(
        r"C:\WINDOWS\explorer.EXE C:\Users\Administrator\Desktop\example-folder"
    )
    assert not command_looks_like_gui_launch("python -m py_compile app.py")
