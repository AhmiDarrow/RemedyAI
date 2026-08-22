# C# in Godot 4

## Requirements
- The **.NET** build of the editor (download name contains `mono` / `.NET`);
  the standard build cannot open C# projects. `game_project_info` reports
  `lang: csharp` or `mixed`; check the binary name too.
- .NET SDK 8 (6 works for 4.0–4.2). `dotnet --version` must succeed.
- `*.csproj` + `*.sln` in the project root, created by the editor on the
  first C# script (Project → Tools → C# → Create C# solution) or by hand:
```xml
<Project Sdk="Godot.NET.Sdk/4.3.0">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <EnableDynamicLoading>true</EnableDynamicLoading>
  </PropertyGroup>
</Project>
```
Sdk version = engine version.

## Build before run
Headless runs do not compile C#; `dotnet build` first:
```text
dotnet build            # in project root; exit 0 required
godot --headless --path . -s tools/smoke_boot.gd
```
`godot --headless --path . --build-solutions --quit` also works but is slower.
`.godot/mono/` is cache; gitignored with the rest of `.godot/`.

## Script shape
```csharp
using Godot;

public partial class Player : CharacterBody2D
{
    [Export] public float Speed { get; set; } = 220f;
    [Signal] public delegate void DiedEventHandler(string cause);
    private Sprite2D _sprite;

    public override void _Ready() => _sprite = GetNode<Sprite2D>("Sprite2D");

    public override void _PhysicsProcess(double delta)
    {
        var dir = Input.GetAxis("move_left", "move_right");
        Velocity = new Vector2(dir * Speed, Velocity.Y + 980f * (float)delta);
        MoveAndSlide();
        if (IsOnFloor() && Input.IsActionJustPressed("jump"))
            Velocity = Velocity with { Y = -420f };
    }

    private void Die() => EmitSignal(SignalName.Died, "spikes");
}
```
Rules: class must be `partial`; file name matches class name; signals are
`delegate ... EventHandler` emitted via `SignalName.X`; `GetNode<T>` not `$`;
`await ToSignal(GetTree().CreateTimer(0.5), SceneTreeTimer.SignalName.Timeout)`.

## Mixing with GDScript
- GDScript → C#: `node.call("MethodName")`; C# members are PascalCase from
  both sides.
- C# → GDScript: `node.Call("method_name")`, `node.Get("prop")`.
- Keep one language per system; cross-calls are untyped and slow to debug.

## Headless / CI caveats
- Smoke scripts can stay GDScript in a C# project; the .NET build runs both.
- Web export does not support C# in 4.x. Android/iOS C# support arrived
  late in 4.x and needs matching templates.
- Tests: gdUnit4 has C# support (`gdUnit4.api` NuGet); GUT is GDScript only.
- `dotnet build` warnings about `Godot.SourceGenerators` usually mean the
  Sdk version mismatches the editor; align them.
- CI: `setup-godot` with `use-dotnet: true`, plus `actions/setup-dotnet`.
