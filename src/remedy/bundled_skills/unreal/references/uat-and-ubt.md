# UAT and UBT

`<Engine>` = engine root, e.g. `%PROGRAMFILES%\Epic Games\UE_5.4`. All
batch files live in `<Engine>\Engine\Build\BatchFiles\`. On macOS/Linux the
equivalents are `Build.sh` / `RunUAT.sh` in the same folder. Check `-help`
on any command before using a flag not listed here.

## UnrealBuildTool (compile)

```
Build.bat <Target> <Platform> <Config> -Project="<abs>.uproject" -WaitMutex
```

| Piece | Values |
|-------|--------|
| Target | `<Name>` (game), `<Name>Editor`, `<Name>Server`, `<Name>Client` — from `Source/*.Target.cs` |
| Platform | `Win64`, `Mac`, `Linux`, `Android`, `IOS` |
| Config | `Debug`, `DebugGame`, `Development`, `Test`, `Shipping` |

- `-WaitMutex` queues behind another UBT instance instead of failing.
- `Rebuild.bat` / `Clean.bat` take the same arguments.
- Output: `Binaries/Win64/` in the project; intermediate in
  `Intermediate/`. Delete `Intermediate/` and `Binaries/` for a clean slate
  (not `Saved/Config` unless asked).
- Compile errors appear as `<file>(<line>): error C2039: ...`.
- Timing: cold build 10–60 min; use the longest `bash_exec` timeout and
  tell the owner. Do not declare a hang before the timeout.

## Generate project files

Launcher engine: `UnrealVersionSelector.exe /projectfiles "<abs>.uproject"`
(lives in `<Engine>\Engine\Binaries\Win64\`). Any engine:

```
"<Engine>\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe" -projectfiles -project="<abs>.uproject" -game -engine -progress
```

Produces `<Name>.sln`. Needed after adding modules or plugins with code.

## RunUAT BuildCookRun (cook + package)

```
RunUAT.bat BuildCookRun -project="<abs>.uproject" -platform=Win64 -clientconfig=Development -cook -build -stage -pak -archive -archivedirectory="<out>" -noP4 -utf8output -unattended
```

| Flag | Does |
|------|------|
| `-build` | compile game target first (skip if already built) |
| `-cook` | convert Content to platform format (slowest step) |
| `-stage` | lay out files in `Saved/StagedBuilds/` |
| `-pak` | bundle cooked content into `.pak` files |
| `-archive -archivedirectory=` | copy the final package to `<out>` |
| `-clientconfig=` | `Development` / `Shipping` etc. |
| `-iterativecooking` | reuse previous cook (faster loops) |
| `-map=<MapName>` | cook only listed maps |
| `-nocompileeditor` | skip editor target build |
| `-server -serverconfig=` | dedicated server package |

- Output package: `<out>\Windows\<Name>.exe` (folder name may be
  `WindowsNoEditor` on older versions).
- Logs: `<Engine>\Engine\Programs\AutomationTool\Saved\Logs\` and
  `<project>\Saved\Logs\`. Grep `Error:` and `LogInit: Display: Failure`.
- Exit code non-zero on failure; UAT prints `BUILD FAILED` / `BUILD SUCCESSFUL`.
- Shipping packages need `-clientconfig=Shipping` and usually `-prereqs`
  for the redistributable installer.
