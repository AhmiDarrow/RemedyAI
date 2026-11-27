---
name: unity
description: >
  Work on a Unity project from the outside: fingerprint the version, write and
  fix C# MonoBehaviours/ScriptableObjects, run Edit/PlayMode tests and builds
  through the editor's batchmode CLI, wire CI. Use when the project has
  ProjectSettings/ProjectVersion.txt or the owner mentions Unity, prefabs,
  MonoBehaviour, ScriptableObject, or Unity tests. Knowledge-only — no
  dedicated tools; everything goes through bash_exec and file tools.
version: 1.0.0
author: Remedy
tags: [game, unity, csharp, engine, batchmode, tests]
requires: []
tools: [game_project_info, local_discover, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, game_playtest, computer_screenshot]
triggers:
  - "\\b(unity|monobehaviour|prefab|scriptableobject|unity tests?)\\b"
  - "\\bc# (script|class) for (a|the|my) game\\b"
local:
  binaries:
    - id: unity
      names: [Unity, unity, Unity.exe]
      env: [UNITY_EDITOR, UNITY_PATH, UNITY]
---

# Unity (scripts, tests, CI — the editor does the rest)

Most Unity work happens inside the editor GUI: scene layout, prefab wiring,
inspector values. That part cannot be done well from a shell. What can be
done from here is the text and reproducible half: C# scripts, tests,
editor-script builds, CI. When the owner asks for scene work, say so
plainly, do the text half, and tell them exactly what to click.

## Fingerprint (start here)

1. `game_project_info(path)`; then `file_read ProjectSettings/ProjectVersion.txt`
   → `m_EditorVersion: 2022.3.20f1` (exact version the project expects).
2. `Assets/` (all content), `Packages/manifest.json` (package deps, incl.
   `com.unity.test-framework`, `com.unity.inputsystem`), `ProjectSettings/`.
3. No `Library/` → project has never been opened on this machine; the first
   batchmode run will import everything (minutes, not seconds).
4. See `references/project-layout.md` for conventions.

## Find the editor (never a wizard)

Order: `UNITY_EDITOR` / `UNITY_PATH` env → `local_discover` → PATH → Hub
install dirs: `%PROGRAMFILES%\Unity\Hub\Editor\<version>\Editor\Unity.exe`
(Windows), `/Applications/Unity/Hub/Editor/<version>/Unity.app/Contents/MacOS/Unity`,
`~/Unity/Hub/Editor/<version>/Editor/Unity` (Linux). Match `<version>` to
`m_EditorVersion`; a different minor version triggers an upgrade prompt and
may rewrite assets — do not run one without asking.

**Not installed?** Say so in one line, then still write the C#, tests and
editor scripts. They are valid without an editor; the owner compiles when
they open the project. Do not pretend a test ran.

## Batchmode CLI (headless oracle)

```
Unity.exe -batchmode -nographics -quit -projectPath <p> -executeMethod <Class.Method> -logFile <f>
```

- Always pass `-logFile <path>`; read it afterwards — exit code alone hides
  compile errors. Grep for `error CS`, `Exception`, `Scripts have compiler errors`.
- Compile check without doing anything: run with `-quit` and no
  `-executeMethod`; a non-zero exit or `error CS` in the log means broken scripts.
- Budget: imports + domain reload take minutes. Give `bash_exec` a long
  timeout; never conclude "hung" under 10 minutes on a cold `Library/`.
- One editor instance per project at a time; if the owner has it open the
  log says so — ask them to close it or skip the CLI step.
- Details and more flags: `references/batchmode-cli.md`. If a flag is not
  listed there, check the Unity manual page "Command line arguments" rather
  than guessing.

## Tests (Unity Test Framework)

```
Unity.exe -batchmode -nographics -projectPath <p> -runTests -testPlatform EditMode -testResults <abs>.xml -logFile <f>
```

- `EditMode` for pure logic (fast, no scene), `PlayMode` for behaviour that
  needs the player loop. Do **not** add `-quit` with `-runTests`.
- Results are NUnit XML; parse `<test-case result="Failed">`. See
  `references/test-runner.md` for asmdef layout and `[UnityTest]` coroutines.
- Keep game logic in plain C# classes (no `MonoBehaviour`) where possible so
  EditMode tests cover it without a scene.

## Builds

Write a static editor method under `Assets/Editor/` that calls
`BuildPipeline.BuildPlayer(...)`, then invoke it with `-executeMethod
BuildScript.BuildWindows`. Template in `references/batchmode-cli.md`. Surface
the `BuildReport.summary.result` and exit non-zero on failure so CI notices.

## Scripting

Patterns, lifecycle order, ScriptableObject data, Input System, and common
mistakes live in `references/scripting-patterns.md`. Rules of thumb:

- One class per file, file name == class name, or Unity will not attach it.
- Never `new` a MonoBehaviour; use `AddComponent` / prefabs.
- Cache `GetComponent` in `Awake`; do work in `Start`; per-frame in `Update`;
  physics in `FixedUpdate` with `Time.fixedDeltaTime`.
- Serialized fields: `[SerializeField] private` over public.
- After editing scripts, the owner's editor recompiles on focus; `.meta`
  files are generated — do not hand-write GUIDs.

## Playtest

No headless playtest. If the owner has a built player (`.exe`), use
`game_playtest(command=<exe>, seconds=…, question=…)`. Otherwise ask them to
press Play and describe/screenshot; `computer_screenshot` works when the
editor is on screen.

## Refuse / redirect

- Scene or prefab surgery by editing `.unity`/`.prefab` YAML: only for tiny,
  well-understood changes (a serialized value); otherwise guide the owner.
- Upgrading the editor version, installing modules, or the Hub: describe the
  step; do not run installers unasked.
- Asset Store downloads, licences, cloud build: owner's job.

## Checklist

```text
[ ] ProjectVersion.txt read; editor located or "not installed" stated
[ ] scripts written; -batchmode -quit compile check (log grepped)
[ ] -runTests EditMode (PlayMode if needed); XML parsed
[ ] build via editor script when asked; report artifact path
[ ] tell the owner what still needs the GUI
```
