import asyncio
import json

import httpx

from src.api.app import create_app
from src.config.api_settings import ApiSettings


def test_existing_chat_still_uses_structural_contract(tmp_path):
    path=tmp_path/"screen_index.json"
    path.write_text(json.dumps({"screens": [{"route": "/erp/withholding", "title": "Retenciones", "main_visible_text": "Consulta de retenciones"}]}), encoding="utf-8")
    app=create_app(ApiSettings(screen_index_path=path, minimum_score=2.0))
    async def ask():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/chat", json={"question": "¿Dónde consulto retenciones?", "conversationId": "regression"})
    payload=asyncio.run(ask()).json()
    assert payload["status"] == "answered"
    assert payload["sources"][0]["route"] == "/erp/withholding"
