# vibe-remote migration shim

`vibe-remote` is the legacy PyPI distribution name for Avibe.

During the 3.0.x compatibility window, this shim is published with the same
version as `avibe-os` and pins that exact `avibe-os` version. This keeps update
notifications and installed versions aligned for users still running clients
that check the legacy `vibe-remote` PyPI feed.
