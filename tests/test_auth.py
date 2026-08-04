"""Tests for login/logout/me and role-based access control.

`client` (see conftest.py) is pre-authenticated as admin; these tests use
`auth_client`/`anon_client` instead so they control the session explicitly.
"""
from src.auth.seed import DEMO_USERS

ADMIN_EMAIL, _ADMIN_NAME, ADMIN_PASSWORD, _ADMIN_ROLE = DEMO_USERS[0]


def test_login_valid_sets_cookie_and_returns_user(anon_client):
    resp = anon_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == ADMIN_EMAIL
    assert body["role"] == "admin"
    assert "hashed_password" not in body
    assert "miq_session" in resp.cookies


def test_login_wrong_password_401(anon_client):
    resp = anon_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_email_401(anon_client):
    resp = anon_client.post("/api/auth/login", json={"email": "ghost@maintainiq.local", "password": "x"})
    assert resp.status_code == 401


def test_me_requires_session(anon_client):
    assert anon_client.get("/api/auth/me").status_code == 401


def test_me_after_login(anon_client):
    anon_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    resp = anon_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == ADMIN_EMAIL


def test_logout_clears_session(anon_client):
    anon_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert anon_client.get("/api/auth/me").status_code == 200

    assert anon_client.post("/api/auth/logout").status_code == 200
    assert anon_client.get("/api/auth/me").status_code == 401


def test_protected_route_requires_auth(anon_client):
    assert anon_client.get("/api/machines").status_code == 401


def test_protected_route_ok_when_logged_in(auth_client):
    resp = auth_client("operator").get("/api/machines")
    assert resp.status_code == 200


def test_users_list_forbidden_for_non_admin(auth_client):
    for role in ("supervisor", "operator"):
        resp = auth_client(role).get("/api/users")
        assert resp.status_code == 403


def test_users_list_ok_for_admin(auth_client):
    resp = auth_client("admin").get("/api/users")
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert emails == {email for email, *_ in DEMO_USERS}


def test_admin_can_create_and_update_user(auth_client):
    admin = auth_client("admin")
    resp = admin.post("/api/users", json={
        "email": "new.tech@maintainiq.local",
        "name": "New Tech",
        "password": "Password123!",
        "role": "operator",
    })
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    resp = admin.patch(f"/api/users/{user_id}", json={"role": "supervisor", "is_active": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "supervisor"
    assert body["is_active"] is False


def test_admin_create_duplicate_email_409(auth_client):
    admin = auth_client("admin")
    resp = admin.post("/api/users", json={
        "email": ADMIN_EMAIL, "name": "Dup", "password": "x", "role": "operator",
    })
    assert resp.status_code == 409


def test_deactivated_user_cannot_authenticate(auth_client):
    admin = auth_client("admin")
    operator_email, _n, operator_password, _r = DEMO_USERS[2]
    users = admin.get("/api/users").json()
    operator = next(u for u in users if u["email"] == operator_email)

    admin.patch(f"/api/users/{operator['id']}", json={"is_active": False})

    resp = admin.post("/api/auth/login", json={"email": operator_email, "password": operator_password})
    assert resp.status_code == 401
