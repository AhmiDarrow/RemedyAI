# Connect Noise_IK fixtures (Phase 5)

Exact hex vectors captured from **live Python** ``remedy.connect.noise``
and ``remedy.connect.record`` before any Go port. Snow crate vectors mirror
Android ``NoiseIkTest.snowVectorIkChaChaBlake2s``.

## Android hex match

Last capture: **MATCHED**

## Files

| File | Vector name | Meaning |
|------|-------------|---------|
| `noise_ik_android_debug_hash.json` | `android_debug_hash` | Intermediate h0 / prologue / rs / e / k / enc(s) |
| `noise_ik_snow.json` | `snow_ik_chacha_blake2s` | Full IK handshake + post-split AEAD (snow keys) |
| `noise_ik_pair_secret.json` | `remedy_pair_secret` | Product prologue; first payload = 32-byte PS; wrong-ps fail |
| `noise_ik_post_split.json` | `remedy_post_split_aead` | Both-direction transport AEAD after split |
| `record_framing.json` | `remedy_record_framing` | u32be\|nonce12\|ct pack + encrypt_record blobs |
| `noise_ik_index.json` | (meta) | Capture stamp + vector list |

## Regenerating

```text
uv run python -m tests.harness.connect_fixture_capture
uv run pytest tests/test_connect_noise_fixtures.py -q
```

Do not hand-edit hex fields. Re-run capture from Python, then re-verify.
