class RoutePolicy:
    def __init__(self, profile: dict):
        exploration = profile["exploration"]

        self.allowed_routes = exploration.get("allowed_routes", [])
        self.blocked_routes = exploration.get("blocked_routes", [])

    def is_allowed(self, route: str | None) -> bool:
        if not route:
            return False

        if route == "/":
            return False

        if any(blocked in route for blocked in self.blocked_routes):
            return False

        if not self.allowed_routes:
            return True

        return any(
            route == allowed.rstrip("/") or route.startswith(allowed)
            for allowed in self.allowed_routes
        )