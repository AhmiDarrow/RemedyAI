---
name: unreal
description: >
  Work on an Unreal Engine 5 project from the shell: read the .uproject,
  decide C++ vs Blueprint, build with UnrealBuildTool, cook/package with
  RunUAT BuildCookRun, run Automation tests via UnrealEditor-Cmd, generate
  project files. Use when a *.uproject exists or the owner mentions Unreal,
  UE5, Blueprints, UBT or RunUAT. Knowledge-only — bash_exec and file tools.
version: 1.0.0
author: Remedy
tags: [game, unreal, ue5, cpp, blueprint, uat, ubt]
requires: []
tools: [game_project_info, local_discover, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, game_playtest, computer_screenshot]
triggers:
  - "\\b(unreal|ue5|ue4|uproject|blueprints?|unrealbuildtool|runuat|ubt|uat)\\b"
local:
  binaries:
    - id: unreal-editor
      names: [UnrealEditor, UnrealEditor-Cmd, UnrealEditor.exe, UnrealEditor-Cmd.exe]
      env: [UE_ROOT, UE5_ROOT, UNREAL_ENGINE]
---

# Unreal Engine 5 (UBT, UAT, automation)

Unreal projects are big and slow. Plan for long commands, read logs instead
of guessing, and keep the owner informed about what the editor GUI still
has to do (Blueprint graphs, level layout, materials).

## Fingerprint (start here)

1. `game_project_info(path)`, then `file_read <Name>.uproject` (JSON):
   `EngineAssociation` (`"5.4"` for launcher builds, a GUID for source
   builds), `Modules[]` (present ⇒ C++ project; absent ⇒ Blueprint-only),
   `Plugins[]`.
2. `Source/<Name>/<Name>.Build.cs` and `Source/<Name>.Target.cs`,
   `<Name>Editor.Target.cs` → module deps and targets.
3. `Config/DefaultEngine.ini` → default maps, game mode; `Content/` holds
   `.uasset`/`.umap` (binary; never hand-edit).
4. Layout details: `references/project-layout.md`.

## Find the engine (never a wizard)

Order: `UE_ROOT` / `UE5_ROOT` env → `local_discover` → well-known dirs:
`%PROGRAMFILES%\Epic Games\UE_<ver>\` (Windows launcher),
`/Users/Shared/Epic Games/UE_<ver>/` (macOS), or the owner's source
checkout. Inside: `Engine\Binaries\Win64\UnrealEditor.exe`,
`UnrealEditor-Cmd.exe`, `Engine\Build\BatchFiles\RunUAT.bat`, `Build.bat`,
`GenerateProjectFiles.bat` (source builds only). Match `<ver>` to
`EngineAssociation`. Not found → ask once: "where is the engine installed?"
and carry on with the C++ you can write anyway.

## C++ vs Blueprint

Blueprint for designer-facing behaviour, quick wiring, anything the owner
will tweak in the editor. C++ for systems, performance, anything you must
test or version-control readably. You can write and build C++; you cannot
author Blueprint graphs from a shell — describe nodes for the owner.
Expose C++ to BP with `UCLASS(Blueprintable)`, `UPROPERTY(EditAnywhere,
BlueprintReadWrite)`, `UFUNCTION(BlueprintCallable)`. More:
`references/cpp-vs-blueprint.md`.

## Build (UnrealBuildTool)

```
"<Engine>\Build\BatchFiles\Build.bat" <Name>Editor Win64 Development -Project="<abs>\<Name>.uproject" -WaitMutex
```

- `<Name>Editor` target to compile for the editor; `<Name>` for a game
  binary. Configs: `Debug`, `DebugGame`, `Development`, `Shipping`.
- First build of a module-heavy project: 10–60 min. Set `bash_exec` timeout
  to the max and say so. Incremental builds: seconds to minutes.
- Errors: grep the output for `error C` (MSVC) and `: error` lines; UBT
  prints the failing file:line.
- Regenerate IDE files after adding modules: right-click the `.uproject` →
  "Generate Visual Studio project files", or
  `UnrealBuildTool -projectfiles -project="<abs>.uproject" -game -engine`
  (see `references/uat-and-ubt.md`).

## Cook / package (RunUAT)

```
"<Engine>\Build\BatchFiles\RunUAT.bat" BuildCookRun -project="<abs>\<Name>.uproject" -platform=Win64 -clientconfig=Development -cook -build -stage -pak -archive -archivedirectory="<out>"
```

Add `-noP4 -utf8output -unattended` for CI. Cooking is the slow part; log
lands under `Engine\Programs\AutomationTool\Saved\Logs\`. Flag details and
what each stage does: `references/uat-and-ubt.md`. Unsure about a flag?
`RunUAT.bat BuildCookRun -help`.

## Automation tests

```
"<Engine>\Binaries\Win64\UnrealEditor-Cmd.exe" "<abs>\<Name>.uproject" -ExecCmds="Automation RunTests <Filter>;Quit" -unattended -nopause -nullrhi -log -ReportOutputPath="<out>"
```

`<Filter>` is a prefix like `MyGame.` or `Project`. Results: JSON report
under `-ReportOutputPath` and `Saved/Logs/<Name>.log`. Writing tests with
`IMPLEMENT_SIMPLE_AUTOMATION_TEST`: `references/automation-tests.md`.

## Playtest

A packaged build (`<out>\Windows\<Name>.exe`) works with
`game_playtest(command=…, seconds=…, question=…)`. Running the editor
itself (`UnrealEditor.exe <uproject> -game`) also works but takes long to
start; pass a generous `seconds`. Otherwise ask the owner to PIE and
screenshot.

## Refuse / redirect

- Hand-editing `.uasset`/`.umap`: never. Describe the editor steps.
- Installing the engine, Visual Studio workloads, or platform SDKs: describe;
  do not run installers unasked.
- Source-engine builds (`Setup.bat`, `GenerateProjectFiles.bat`, full engine
  compile): only on explicit request; hours.

## Checklist

```text
[ ] .uproject read: EngineAssociation, Modules (C++?) noted
[ ] engine located or "not found" stated; C++ written regardless
[ ] Build.bat <Name>Editor Win64 Development — errors grepped
[ ] Automation RunTests when tests exist
[ ] BuildCookRun only when a package is asked for; report output dir
[ ] list what still needs the editor GUI
```
