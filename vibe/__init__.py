"""avibe - local-first Agent OS runtime."""

try:
    from vibe._version import __version__
except ImportError:
    __version__ = "0.0.0.dev0"  # Fallback for editable installs without build
