# C++ vs Blueprint

## Decide

| Put it in C++ | Put it in Blueprint |
|---------------|---------------------|
| core systems, inventories, AI logic, networking | level scripting, UI flow, tweakable gameplay values |
| anything needing unit tests or code review | rapid prototyping the owner iterates on in-editor |
| hot loops, large data | visual FX hookups, animation events |
| base classes designers subclass | the subclasses themselves |

Common pattern: C++ base class exposes variables and events; a Blueprint
child sets values and reacts. You write the C++; the owner makes the
Blueprint child (Content Browser → right-click → Blueprint Class → pick the
C++ parent).

## Exposing C++

```cpp
#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "HeroCharacter.generated.h"

UCLASS(Blueprintable)
class MYGAME_API AHeroCharacter : public ACharacter
{
    GENERATED_BODY()
public:
    AHeroCharacter();

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Stats")
    float MaxHealth = 100.f;

    UFUNCTION(BlueprintCallable, Category="Stats")
    void ApplyDamage(float Amount);

    UFUNCTION(BlueprintImplementableEvent, Category="Stats")
    void OnDied();                     // body lives in Blueprint

    UFUNCTION(BlueprintNativeEvent, Category="Stats")
    void OnDamaged(float Amount);      // C++ default, BP may override

protected:
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
};
```

`MYGAME_API` is `<MODULE>_API` in caps. `*.generated.h` must be the last
include. `BlueprintNativeEvent` needs a `OnDamaged_Implementation` body.

## Specifier cheat sheet

- `UPROPERTY`: `EditAnywhere` / `EditDefaultsOnly` / `VisibleAnywhere`;
  `BlueprintReadWrite` / `BlueprintReadOnly`; `Category`; `Replicated`;
  `meta=(ClampMin="0")`.
- `UFUNCTION`: `BlueprintCallable`, `BlueprintPure`,
  `BlueprintImplementableEvent`, `BlueprintNativeEvent`, `Server`,
  `Client`, `NetMulticast`, `Reliable`.
- `UCLASS`: `Blueprintable`, `BlueprintType`, `Abstract`, `Config=Game`.

## Naming

`A` prefix for actors, `U` for UObjects/components, `F` for structs, `E`
for enums, `I` for interfaces, `T` for templates. Wrong prefix ⇒ UHT error.

## Common mistakes

- Forgetting `GENERATED_BODY()` or the `.generated.h` include.
- Raw `new` for UObjects — use `NewObject<>`, `CreateDefaultSubobject<>`
  in constructors, `SpawnActor<>` for actors.
- Holding `UObject*` without `UPROPERTY` ⇒ garbage-collected under you.
- Using `TEXT("...")` everywhere is correct; bare string literals to
  `FString` trigger warnings.
- Heavy logic in `Tick` — use timers (`GetWorldTimerManager().SetTimer`).
- Adding a module dependency in code but not in `Build.cs` ⇒ link error.
- Changing a `UPROPERTY` type after Blueprints reference it ⇒ the owner must
  re-save affected assets in the editor.
