# C# scripting patterns

## Lifecycle order

`Awake` (self setup, cache components) → `OnEnable` → `Start` (talk to
others) → `FixedUpdate` (physics, fixed step) → `Update` (per frame) →
`LateUpdate` (camera follow) → `OnDisable` → `OnDestroy`.
Awake runs even if the component is disabled; Start does not.

## Skeleton

```csharp
using UnityEngine;

[RequireComponent(typeof(Rigidbody2D))]
public class PlayerMover : MonoBehaviour
{
    [SerializeField] private float speed = 5f;
    private Rigidbody2D rb;
    private Vector2 input;

    private void Awake() { rb = GetComponent<Rigidbody2D>(); }

    private void Update()
    {
        input = new Vector2(Input.GetAxisRaw("Horizontal"), Input.GetAxisRaw("Vertical")).normalized;
    }

    private void FixedUpdate()
    {
        rb.linearVelocity = input * speed;   // `velocity` on Unity < 6
    }
}
```

`rb.linearVelocity` is Unity 6+; older editors use `rb.velocity`. Check
`m_EditorVersion` before choosing.

## ScriptableObject data

```csharp
[CreateAssetMenu(menuName = "Game/Enemy Stats")]
public class EnemyStats : ScriptableObject
{
    public int hp = 10;
    public float speed = 2f;
}
```

Reference it from a MonoBehaviour via `[SerializeField] EnemyStats stats;`.
The owner creates the `.asset` via the Create menu; from a shell you need
an editor script (`AssetDatabase.CreateAsset`).

## Input

- Legacy: `Input.GetAxis`, `Input.GetKeyDown(KeyCode.Space)`.
- New Input System (`com.unity.inputsystem` in manifest): generate a C# class
  from the `.inputactions` asset or use `PlayerInput` + `OnMove(InputValue v)`.
  Mixing both needs "Active Input Handling: Both" in Player settings.

## Coroutines and timing

```csharp
IEnumerator Flash() { sr.color = Color.red; yield return new WaitForSeconds(0.1f); sr.color = Color.white; }
StartCoroutine(Flash());
```

Use `Time.deltaTime` in `Update`, `Time.fixedDeltaTime` in `FixedUpdate`.
Async/await works but is not cancelled on destroy — prefer coroutines for
object-bound work.

## Events and decoupling

`UnityEvent` for inspector-wired callbacks; C# `event Action<T>` for code.
Unsubscribe in `OnDisable`. Avoid `FindObjectOfType` in hot paths
(`FindFirstObjectByType` in Unity 2023+).

## Common mistakes

- File name ≠ class name → component will not attach.
- `new MonoBehaviour()` → warning, broken object. Use `AddComponent`.
- `GetComponent` every frame → cache it.
- Setting transform on a Rigidbody → use `rb.MovePosition`.
- `Destroy` inside a loop over the collection being iterated.
- Missing `[System.Serializable]` on nested classes shown in the inspector.
- Public fields for everything: prefer `[SerializeField] private`.
- Code under `Editor/` referenced from runtime → build fails; wrap with
  `#if UNITY_EDITOR` only for genuinely editor-only paths.
