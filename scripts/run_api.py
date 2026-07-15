from __future__ import annotations

import uvicorn

from src.config.api_settings import ApiSettings


def main() -> None:
    settings = ApiSettings()
    uvicorn.run("src.api.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
