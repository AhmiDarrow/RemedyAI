# Connect Noise / record fixtures

Exact hex vectors captured from live Python ``remedy.connect.noise`` and
``remedy.connect.record`` **before** that package was retired. Go
(``native/go/connect``) and Android ``connect-core`` own verification now.

Snow crate vectors mirror the Noise_IK handshake shape; product keys after
``Split`` match the record framing blobs.

Re-verify with:

```text
go test ./connect/ -count=1
```

from ``native/go``. Do not reintroduce a Python Connect Noise implementation.
