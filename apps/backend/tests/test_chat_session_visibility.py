def test_chat_session_list_uses_the_active_database_profile(client):
    user_session = client.post(
        "/api/v1/chat/sessions",
        json={"title": "分析一下最近石油的动向", "mode": "chat"},
    ).json()
    system_named_session = client.post(
        "/api/v1/chat/sessions",
        json={"title": "local v0.1 live acceptance SAMPLE", "mode": "chat"},
    ).json()

    response = client.get("/api/v1/chat/sessions")
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()}
    assert user_session["id"] in items
    assert system_named_session["id"] in items
    assert all("is_system_test" not in item for item in items.values())
