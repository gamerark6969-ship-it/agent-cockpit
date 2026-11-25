"""YouTube automation package; importing models registers its migration metadata."""

from . import models as models

__all__ = ["AutomationService", "models", "router", "public_router"]


def __getattr__(name):
    if name == "AutomationService":
        from .service import AutomationService
        return AutomationService
    if name in ("router", "public_router"):
        from . import api
        return getattr(api,name)
    raise AttributeError(name)
