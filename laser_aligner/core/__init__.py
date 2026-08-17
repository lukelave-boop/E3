"""UI-neutral lifecycle and service boundaries."""

from .runtime import CoreRuntime, RuntimeSnapshot, RuntimeState

__all__ = ["CoreRuntime", "RuntimeSnapshot", "RuntimeState"]
