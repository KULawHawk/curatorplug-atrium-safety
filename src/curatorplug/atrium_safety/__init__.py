"""Constitutional compliance plugin for Curator.

Enforces Atrium Constitution Principle 2 (Hash-Verify-Before-Move) as a
*cross-cutting layer* over Curator's plugin ecosystem. See ``DESIGN.md``
for the full design and the four ratified DMs.

Public API:

* :class:`ComplianceError` -- raised by the plugin when a write violates
  Constitution Principle 2 (e.g., when ``--no-verify-hash`` was passed
  and the plugin is configured for strict enforcement).
* :class:`AtriumSafetyPlugin` -- the Pluggy hookimpl class. Registered
  automatically via setuptools entry point under
  ``[project.entry-points.curator]``. Users normally never instantiate
  this directly.

Anything not listed above is internal and may change between patch
releases. ``verifier.py`` and ``enforcer.py`` are utility modules used
by the plugin and exposed for unit testing; they are NOT part of the
stable API.
"""

from __future__ import annotations

from curatorplug.atrium_safety.exceptions import ComplianceError
from curatorplug.atrium_safety.plugin import AtriumSafetyPlugin

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ComplianceError",
    "AtriumSafetyPlugin",
]
