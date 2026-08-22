# Audio

## Buses (`default_bus_layout.tres`)
`Master` → `Music`, `SFX`, `UI` (optionally `Ambience`). Settings sliders
set bus volume, never per-player volume:
```gdscript
func set_bus_volume(bus: String, linear: float) -> void:
    var idx := AudioServer.get_bus_index(bus)
    AudioServer.set_bus_volume_db(idx, linear_to_db(clampf(linear, 0.0001, 1.0)))
    AudioServer.set_bus_mute(idx, linear < 0.001)
```
Store linear 0–1 in the save; convert on apply.

## SFX pool (autoload `Audio`)
Instantiating an `AudioStreamPlayer` per shot leaks nodes and stutters.
Pool 8–16 players:
```gdscript
extends Node
const POOL := 12
var _players: Array[AudioStreamPlayer] = []
var _i := 0

func _ready() -> void:
    for n in POOL:
        var p := AudioStreamPlayer.new()
        p.bus = "SFX"
        add_child(p)
        _players.append(p)

func play(stream: AudioStream, pitch_jitter: float = 0.1, volume_db: float = 0.0) -> void:
    var p := _players[_i]
    _i = (_i + 1) % POOL
    p.stream = stream
    p.pitch_scale = randf_range(1.0 - pitch_jitter, 1.0 + pitch_jitter)
    p.volume_db = volume_db
    p.play()
```
Positional sound: `AudioStreamPlayer2D` on the emitting node instead, with
`max_distance` set; still keep a pool for common one-shots.
`AudioStreamRandomizer` picks from variants with pitch/volume spread; use
it for footsteps and hits.

## Music
One `AudioStreamPlayer` on the `Music` bus in an autoload. Loop via the
OGG import flag (`loop=true`), not code. Crossfade with two players and a
`Tween` on `volume_db` (-80 → 0). For layered/adaptive music use
`AudioStreamInteractive` (4.3+) or `AudioStreamSynchronized`.

## Formats and import
- SFX: WAV 16-bit mono 44.1 kHz; import `compress/mode` = "PCM" for
  short, "IMA ADPCM" or "Quite OK Audio" (4.3+) to shrink long ones.
- Music/ambience: OGG Vorbis, 128–192 kbps; `loop=true`, `loop_offset` for
  intro-then-loop tracks.
- MP3 works but has encoder delay at loop points; avoid for loops.
- Web: keep total audio under a few MB; OGG only.

## Mixing defaults
Music -8 to -12 dB under SFX; UI clicks -6 dB; never let SFX clip on the
Master (add a `Limiter` effect on Master at -0.5 dB). Duck music by 4–6 dB
on big hits via a short tween on the Music bus.

## Polish list (milestone 3)
Jump, land, hit, hurt, die, pickup, UI move/accept/cancel, level start,
level win. Each with 2–3 variants or pitch jitter. Silence is the loudest
bug: a diag script that checks every `sfx_*` referenced in scripts exists
in `assets/audio/sfx/` catches it.

## Headless
Audio is disabled under `--headless`; `play()` is a no-op, no errors.
Check that streams load (`load() != null`) in the diag script; check that
they sound right in the owner's playtest.
