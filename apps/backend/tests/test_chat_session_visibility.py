def test_chat_session_list_uses_the_active_database_profile(client, db):
    from sqlalchemy import event

    user_session = client.post(
        "/api/v1/chat/sessions",
        json={"title": "分析一下最近石油的动向", "mode": "chat"},
    ).json()
    system_named_session = client.post(
        "/api/v1/chat/sessions",
        json={"title": "local v0.1 live acceptance SAMPLE", "mode": "chat"},
    ).json()

    message = client.post(
        f"/api/v1/chat/sessions/{user_session['id']}/messages",
        json={"role": "user", "content": "A test question"},
    )
    assert message.status_code == 200
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        response = client.get("/api/v1/chat/sessions")
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()}
    assert user_session["id"] in items
    assert system_named_session["id"] in items
    assert items[user_session["id"]]["message_count"] == 1
    assert items[system_named_session["id"]]["message_count"] == 0
    message_reads = [statement.lower() for statement in statements if "from chat_messages" in statement.lower()]
    assert len(message_reads) == 1
    assert "count(" in message_reads[0]
    assert "chat_messages.content" not in message_reads[0]
    assert all("is_system_test" not in item for item in items.values())
