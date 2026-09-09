import asyncio

import pytest

from tests.conftest import register_login

pytestmark = pytest.mark.integration


async def test_ownership_and_audit(client):
    alice = await register_login(client)
    bob = await register_login(client, "bob@example.com")
    a = {"Authorization": "Bearer " + alice["access_token"]}
    b = {"Authorization": "Bearer " + bob["access_token"]}
    response = await client.post("/api/v1/notes", headers=a, json={"title": "Private", "body": "Secret"})
    assert response.status_code == 201
    note = response.json()
    assert (await client.get(f"/api/v1/notes/{note['id']}", headers=b)).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/notes/{note['id']}", headers=b, json={"expected_version": 1, "title": "Hijacked"}
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/notes/{note['id']}?expected_version=1", headers=b)
    ).status_code == 404
    assert (await client.get("/api/v1/notes", headers=b)).json()["items"] == []
    audit = await client.get("/api/v1/audit", headers=a)
    assert any(event["action"] == "note.created" for event in audit.json())
    assert "Secret" not in audit.text


async def test_note_input_cannot_override_owner(client):
    pair = await register_login(client)
    response = await client.post(
        "/api/v1/notes",
        headers={"Authorization": "Bearer " + pair["access_token"]},
        json={"title": "Note", "owner_id": "fake"},
    )
    assert response.status_code == 422


async def test_optimistic_concurrency(client):
    pair = await register_login(client)
    headers = {"Authorization": "Bearer " + pair["access_token"]}
    note = (await client.post("/api/v1/notes", headers=headers, json={"title": "Initial"})).json()
    responses = await asyncio.gather(
        *[
            client.patch(
                f"/api/v1/notes/{note['id']}", headers=headers, json={"expected_version": 1, "title": title}
            )
            for title in ["First", "Second"]
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        await client.delete(f"/api/v1/notes/{note['id']}?expected_version=1", headers=headers)
    ).status_code == 409
    assert (
        await client.delete(f"/api/v1/notes/{note['id']}?expected_version=2", headers=headers)
    ).status_code == 204


async def test_cursor_pagination_and_limits(client):
    pair = await register_login(client)
    headers = {"Authorization": "Bearer " + pair["access_token"]}
    for i in range(3):
        await client.post("/api/v1/notes", headers=headers, json={"title": f"Note {i}"})
    first = (await client.get("/api/v1/notes?limit=2", headers=headers)).json()
    second = (
        await client.get(
            "/api/v1/notes", headers=headers, params={"limit": 2, "cursor": first["next_cursor"]}
        )
    ).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {item["id"] for item in first["items"]}.isdisjoint(item["id"] for item in second["items"])
    assert (await client.get("/api/v1/notes?limit=101", headers=headers)).status_code == 422
    assert (await client.get("/api/v1/notes?cursor=invalid", headers=headers)).status_code == 422
