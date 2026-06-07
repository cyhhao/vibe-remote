# vibe-remote migration shim

`vibe-remote` is the legacy PyPI distribution name for avibe.

This one-time shim release depends on `avibe-os>=3.0.0` and keeps the `vibe`
console command available for existing users who upgrade from the old package
name. New releases should be published as `avibe-os`.
