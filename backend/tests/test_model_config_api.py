from fastapi.testclient import TestClient

from app.config import model_config as model_config_module
from app.main import app


def _profile(profile_id: str, name: str, model: str, api_key: str, base_url: str) -> dict:
    # 中文注释：测试直接使用 API 结构，覆盖前端保存时的真实载荷。
    endpoint = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "temperature": 0.4,
        "timeout_seconds": 12,
        "max_retries": 2,
        "thinking_mode": "disabled",
    }
    return {
        "id": profile_id,
        "name": name,
        "llm": endpoint,
        "summary": {**endpoint, "model": f"{model}-summary", "temperature": 0.1},
        "embedding": {**endpoint, "model": f"{model}-embedding", "temperature": 0},
        "rerank": {**endpoint, "model": f"{model}-rerank", "temperature": 0, "timeout_seconds": 8},
        "memory_summary_enabled": True,
    }


def test_model_config_save_and_activate_updates_runtime_settings(tmp_path, monkeypatch):
    config_path = tmp_path / "model_profiles.json"
    monkeypatch.setattr(model_config_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr("app.api.model_config.CONFIG_PATH", config_path, raising=False)

    payload = {
        "active_profile_id": "deepseek",
        "profiles": [
            _profile("deepseek", "DeepSeek", "deepseek-chat", "sk-deepseek", "https://deepseek.example/v1"),
            _profile("local", "Local", "local-model", "sk-local", "http://127.0.0.1:11434/v1"),
        ],
    }

    client = TestClient(app)
    save_resp = client.put("/api/model-config", json=payload)

    assert save_resp.status_code == 200
    assert config_path.exists()
    assert model_config_module.settings.llm_model == "deepseek-chat"
    assert model_config_module.settings.llm_api_key == "sk-deepseek"
    assert model_config_module.settings.llm_base_url == "https://deepseek.example/v1"
    assert model_config_module.settings.memory_summary_model == "deepseek-chat-summary"
    assert model_config_module.settings.embedding_model == "deepseek-chat-embedding"
    assert model_config_module.settings.rerank_model == "deepseek-chat-rerank"

    active_resp = client.post("/api/model-config/active", json={"active_profile_id": "local"})

    assert active_resp.status_code == 200
    assert active_resp.json()["active_profile_id"] == "local"
    assert model_config_module.settings.llm_model == "local-model"
    assert model_config_module.settings.llm_base_url == "http://127.0.0.1:11434/v1"
