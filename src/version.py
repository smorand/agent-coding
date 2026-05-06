"""Version injection point.

Default value used during development; the build pipeline overwrites
this file with the git tag value before packaging or building the
Docker image (see the `build` and `docker-build` targets in the
Makefile, and the `APP_VERSION` build arg in the Dockerfile).
"""

from __future__ import annotations

__version__: str = "dev"

__all__ = ["__version__"]
