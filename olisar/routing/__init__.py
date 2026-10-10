"""Model routing: pick a model for each reply by what the message needs.

See olisar.routing.policy for the tiers and the rules, olisar.routing.classifier for the
scores, and design/model-routing/README.md for why it's built this way.
"""

from olisar.routing.policy import DEEP, LIGHT, STANDARD, Route, Routing, choose, enabled, finish

__all__ = ["DEEP", "LIGHT", "STANDARD", "Route", "Routing", "choose", "enabled", "finish"]
