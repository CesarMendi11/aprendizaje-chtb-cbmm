import json
from pathlib import Path


class ScreenIndexBuilder:
    def __init__(self):
        self.screens = {}

    def add_screen(self, route: str, screen_data: dict):
        self.screens[route] = {
            "url": screen_data.get("url"),
            "title": screen_data.get("title"),
            "buttons": [
                item.get("text")
                for item in screen_data.get("buttons", [])
                if item.get("text")
            ],
            "inputs": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "placeholder": item.get("placeholder"),
                    "type": item.get("type"),
                }
                for item in screen_data.get("inputs", [])
            ],
            "tables": [
                {
                    "headers": table.get("headers", []),
                    "rows_count": table.get("rows_count"),
                }
                for table in screen_data.get("tables", [])
            ],
            "artifacts": screen_data.get("artifacts", {}),
        }

    def save(self, output_path: str):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as file:
            json.dump(self.screens, file, indent=2, ensure_ascii=False)