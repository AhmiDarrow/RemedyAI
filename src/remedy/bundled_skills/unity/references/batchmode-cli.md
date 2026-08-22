# Batchmode CLI

All flags below are documented in the Unity manual ("Command line
arguments"). If you need something else, look it up; do not guess.

## Core flags

| Flag | Meaning |
|------|---------|
| `-batchmode` | no GUI, no dialogs; errors go to the log |
| `-nographics` | no GPU needed (CI, servers). Omit for PlayMode tests that render |
| `-quit` | exit after the command finishes. Never combine with `-runTests` |
| `-projectPath <p>` | absolute path to the folder containing `Assets/` |
| `-executeMethod <Class.Method>` | static method in an editor assembly |
| `-logFile <f>` | write the editor log here |
| `-buildTarget <t>` | e.g. `Win64`, `StandaloneLinux64`, `WebGL`, `Android` |

## Compile check

```
"<Unity.exe>" -batchmode -nographics -quit -projectPath "<p>" -logFile "<p>/Logs/compile.log"
```

Then grep the log: `error CS`, `Scripts have compiler errors`, `Exception`.
Exit code 0 with `error CS` in the log still means broken.

## Build script template

`Assets/Editor/BuildScript.cs` (folder named `Editor` ⇒ editor assembly):

```csharp
using UnityEditor;
using UnityEditor.Build.Reporting;
using System.Linq;

public static class BuildScript
{
    static string[] Scenes() =>
        EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray();

    public static void BuildWindows()
    {
        var opts = new BuildPlayerOptions {
            scenes = Scenes(),
            locationPathName = "Builds/Win64/Game.exe",
            target = BuildTarget.StandaloneWindows64,
            options = BuildOptions.None,
        };
        BuildReport r = BuildPipeline.BuildPlayer(opts);
        if (r.summary.result != BuildResult.Succeeded)
            EditorApplication.Exit(1);
    }
}
```

Invoke:

```
"<Unity.exe>" -batchmode -nographics -quit -projectPath "<p>" -executeMethod BuildScript.BuildWindows -buildTarget Win64 -logFile "<p>/Logs/build.log"
```

Scenes must be enabled in Build Settings (`EditorBuildSettings.scenes`) or
listed explicitly.

## Timeouts and locks

- Cold `Library/`: first run imports everything. Minutes to tens of minutes
  on big projects. Set the `bash_exec` timeout generously and say so.
- Only one editor per project. If the owner has it open, batchmode fails
  fast with an "already open" message in the log — ask them to close it.
- Shader compilation during builds is slow even with `-nographics`; normal.

## Licence

Batchmode needs an activated licence on the machine. A log line about
licensing / "No valid Unity Editor license" means the owner must sign in
via the Hub once; you cannot fix that from a shell.
