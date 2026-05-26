class CrawlerFrontier:
    def __init__(self, max_pages: int):
        self.max_pages = max_pages
        self.visited: set[str] = set()
        self.pending: list[str] = []

    def add_many(self, routes: list[str]) -> None:
        for route in routes:
            self.add(route)

    def add(self, route: str) -> None:
        if route in self.visited:
            return

        if route in self.pending:
            return

        self.pending.append(route)

    def has_next(self) -> bool:
        return bool(self.pending) and len(self.visited) < self.max_pages

    def next(self) -> str:
        return self.pending.pop(0)

    def mark_visited(self, route: str) -> None:
        self.visited.add(route)

    def was_visited(self, route: str) -> bool:
        return route in self.visited

    def visited_count(self) -> int:
        return len(self.visited)