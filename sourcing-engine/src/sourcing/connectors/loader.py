"""Dynamic connector instantiation from a registry ``connector_ref`` (plan §2.7).

The registry's ``connector_ref`` (e.g. ``"sourcing.connectors.asic_bulk.ASICBulkConnector"``)
is resolved to a class and instantiated. This is the single seam through which
the rest of the engine obtains a working connector — it never imports concrete
connector classes directly.
"""
from __future__ import annotations

import importlib
from typing import Any


def load_connector(connector_ref: str, **kwargs: Any) -> Any:
    """Import and instantiate the connector named by ``connector_ref``.

    ``connector_ref`` is a fully-qualified ``module.path.ClassName``. Extra
    keyword arguments are forwarded to the constructor (e.g. an injected cache
    or DuckDB path in tests).
    """
    if "." not in connector_ref:
        raise ValueError(f"connector_ref must be 'module.path.ClassName', got: {connector_ref!r}")
    module_path, cls_name = connector_ref.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, cls_name)
    return cls(**kwargs)
