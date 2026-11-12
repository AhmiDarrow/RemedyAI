# Desktop release native CLI assets

GitHub rejected a push of `.github/workflows/desktop-release.yml` because the
`gh` token on this machine has `repo` but not the `workflow` scope.

Apply this on a machine whose token can update workflows, then push:

```bash
git apply scripts/desktop-release-native-assets.patch
git add .github/workflows/desktop-release.yml
git commit -m "ci: Publish standalone runtime/core binaries and SHA256SUMS."
```

After that, the Release job attaches `remedy-runtime-*`, `libremedy_core-*.so` /
`remedy_core-*.dll`, and `remedy-native-SHA256SUMS.txt`, which first-run
`remedy serve` downloads.
