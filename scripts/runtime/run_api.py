from __future__ import annotations

import uvicorn

from erp_assistant.config.api_settings import ApiSettings


def main() -> None:
    settings = ApiSettings()
    uvicorn.run(
        "erp_assistant.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )


if __name__ == "__main__":
    main()
