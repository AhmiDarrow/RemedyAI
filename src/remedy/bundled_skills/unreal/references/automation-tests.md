# Automation tests

Unreal's Automation Framework runs inside the editor process. Tests are
C++ (or Blueprint Functional Tests placed in a level). You can write and
run the C++ kind from a shell.

## Writing a simple test

`Source/<Name>/Tests/InventoryTest.cpp`:

```cpp
#include "Misc/AutomationTest.h"
#include "Inventory.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FInventoryAddTest,
    "MyGame.Inventory.Add",
    EAutomationTestFlags::ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FInventoryAddTest::RunTest(const FString& Parameters)
{
    FInventory Inv;
    Inv.Add(TEXT("key"));
    TestEqual(TEXT("count after add"), Inv.Count(), 1);
    return true;
}
```

- The second argument is the dotted path shown in the Session Frontend and
  used by filters.
- Flags: `ProductFilter` (fast), `SmokeFilter`, `EngineFilter`,
  `StressFilter`; plus a context mask (`ApplicationContextMask` = editor,
  game, client, server). Missing context ⇒ test never appears.
- Test files compile into the game module; guard with
  `#if WITH_DEV_AUTOMATION_TESTS ... #endif` so Shipping skips them.
- Assertion helpers: `TestTrue`, `TestFalse`, `TestEqual`, `TestNotNull`,
  `AddError`, `AddWarning`. Returning `false` or any `AddError` fails.
- World-dependent tests: `FAutomationEditorCommonUtils::CreateNewMap()` or
  latent commands (`ADD_LATENT_AUTOMATION_COMMAND`) for multi-frame work.

## Running headless

```
"<Engine>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "<abs>.uproject" -ExecCmds="Automation RunTests MyGame.Inventory;Quit" -unattended -nopause -nullrhi -log -ReportOutputPath="<out>\automation"
```

- `Automation RunTests <Filter>`: prefix match on the dotted name. Use
  `Automation RunAll` for everything (slow) or `Automation List` to print
  the names.
- `;Quit` makes the editor exit when done. Without it the process stays up.
- `-nullrhi` avoids needing a GPU; drop it for rendering tests.
- `-ReportOutputPath` writes `index.json` with per-test state
  (`"State": "Success" | "Fail"`). Also read `Saved/Logs/<Name>.log` for
  `LogAutomationController` lines and `Test Completed. Result={Passed|Failed}`.
- Rebuild the editor target (`Build.bat <Name>Editor Win64 Development`)
  before running; the commandlet uses the compiled module.
- Startup (shader compile, asset registry) can take minutes; budget for it.

## Exit status

Exit code is not reliable across versions — parse the report JSON or grep
the log for failures, and state what you parsed.

## Gauntlet

`RunUAT.bat RunUnreal -project=... -build=... -test=...` drives packaged
builds for full-game tests. Heavier than you need for unit-level checks;
mention it only when the owner asks for packaged-build testing.
