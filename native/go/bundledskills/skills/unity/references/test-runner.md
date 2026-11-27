# Unity Test Framework

Package `com.unity.test-framework` (check `Packages/manifest.json`). NUnit
underneath.

## Layout

```text
Assets/Tests/EditMode/EditMode.asmdef
Assets/Tests/EditMode/InventoryTests.cs
Assets/Tests/PlayMode/PlayMode.asmdef
Assets/Tests/PlayMode/PlayerMoveTests.cs
```

`EditMode.asmdef`:

```json
{
  "name": "EditMode",
  "references": ["GameRuntime"],
  "includePlatforms": ["Editor"],
  "optionalUnityReferences": ["TestAssemblies"],
  "defineConstraints": ["UNITY_INCLUDE_TESTS"]
}
```

PlayMode: same but `"includePlatforms": []`. `GameRuntime` is whatever asmdef
wraps the code under test. Test assemblies cannot reference the default
`Assembly-CSharp`, so add a runtime asmdef first.

## Writing tests

```csharp
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using System.Collections;

public class InventoryTests
{
    [Test]
    public void Add_IncreasesCount()
    {
        var inv = new Inventory();
        inv.Add("key");
        Assert.AreEqual(1, inv.Count);
    }

    [UnityTest]
    public IEnumerator Player_FallsUnderGravity()
    {
        var go = new GameObject("p", typeof(Rigidbody));
        float y0 = go.transform.position.y;
        yield return new WaitForFixedUpdate();
        yield return new WaitForFixedUpdate();
        Assert.Less(go.transform.position.y, y0);
        Object.Destroy(go);
    }
}
```

`[Test]` for synchronous; `[UnityTest]` returns `IEnumerator` and yields
frames (PlayMode) or editor ticks (EditMode).

## Running headless

```
"<Unity.exe>" -batchmode -nographics -projectPath "<p>" -runTests -testPlatform EditMode -testResults "<abs>/results-edit.xml" -logFile "<p>/Logs/tests.log"
```

- `-testPlatform PlayMode` for the PlayMode assembly. Drop `-nographics` if
  tests need rendering.
- `-testFilter <pattern>` narrows by full test name (check the manual for
  current syntax).
- Do **not** pass `-quit`; the runner exits on its own. Exit code non-zero on
  failures.
- `-testResults` must be an absolute path.

## Reading results

NUnit 3 XML. Quick parse:

```python
import xml.etree.ElementTree as ET
r = ET.parse("results-edit.xml").getroot()
print(r.attrib["total"], r.attrib["failed"])
for tc in r.iter("test-case"):
    if tc.attrib.get("result") == "Failed":
        print(tc.attrib["fullname"], tc.findtext("failure/message"))
```

## Keep logic testable

Put rules in plain classes (`Inventory`, `Damage`, `Pathfinder`) with no
`UnityEngine` dependency except value types. MonoBehaviours become thin
adapters. EditMode tests then run in seconds and need no scene.
