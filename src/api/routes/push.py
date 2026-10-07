"""Web Push subscription endpoints for the mobile operator view
(design/2026-10-07-mobile-operator-pwa-design.md, decision 10).

Open to every signed-in role (app.py adds get_current_user) and always bound
to the caller: a user can only create, refresh, delete or test their own
subscriptions, and endpoints are never returned to anyone else. Handlers are
sync `def`, so the test push runs in the threadpool, never on the event loop
(push.push_to_roles refuses to).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.deps import get_db
from src.api.schemas import (
    PushConfig,
    PushSubscribeRequest,
    PushSubscriptionOut,
    PushTestResult,
    PushUnsubscribeRequest,
)
from src.auth.deps import get_current_user
from src.notifications import push

router = APIRouter(prefix="/push", tags=["push"])

_NOT_CONFIGURED = "push notifications are not configured"
_USER_AGENT_MAX = 255


def _require_settings() -> push.PushSettings:
    settings = push.settings_from_env()
    if settings is None:
        raise HTTPException(503, _NOT_CONFIGURED)
    return settings


@router.get("/vapid-public-key", response_model=PushConfig)
def vapid_public_key(response: Response):
    """The browser's applicationServerKey, or enabled: false when push is off."""
    response.headers["Cache-Control"] = "no-store"
    settings = push.settings_from_env()
    if settings is None:
        return PushConfig(enabled=False, public_key=None)
    return PushConfig(enabled=True, public_key=settings.public_key)


@router.post("/subscribe", response_model=PushSubscriptionOut)
def subscribe(body: PushSubscribeRequest, request: Request, db=Depends(get_db),
              user: dict = Depends(get_current_user)):
    """Upsert on endpoint: the same phone re-subscribing refreshes its keys
    and reactivates an expired row; another user signing in on it rebinds
    the row to them. Always 200 — the client never needs to tell new from
    refreshed."""
    _require_settings()
    if not push.endpoint_allowed(body.endpoint):
        raise HTTPException(400, "endpoint must be an https URL on a known push service")
    if not (push.valid_public_key(body.keys.p256dh) and push.valid_auth_secret(body.keys.auth)):
        raise HTTPException(400, "invalid subscription keys")

    now = datetime.now(timezone.utc).isoformat()
    user_agent = (request.headers.get("user-agent") or "")[:_USER_AGENT_MAX] or None
    db.execute(
        """INSERT INTO push_subscriptions
               (user_id, endpoint, p256dh, auth, user_agent, is_active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET
               user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth,
               user_agent = excluded.user_agent, is_active = 1, deactivated_at = NULL,
               updated_at = excluded.updated_at""",
        (user["id"], body.endpoint, body.keys.p256dh, body.keys.auth, user_agent, now, now),
    )
    db.commit()
    row = db.execute(
        """SELECT id, endpoint, is_active, created_at, updated_at, last_used_at
           FROM push_subscriptions WHERE endpoint = ?""",
        (body.endpoint,),
    ).fetchone()
    return PushSubscriptionOut(**dict(row))


@router.delete("/subscribe", status_code=204)
def unsubscribe(body: PushUnsubscribeRequest, db=Depends(get_db),
                user: dict = Depends(get_current_user)):
    """Hard-delete the caller's row for this endpoint. Always 204: idempotent,
    and it does not reveal whether someone else owns the endpoint."""
    db.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
               (body.endpoint, user["id"]))
    db.commit()
    return Response(status_code=204)


@router.post("/test", response_model=PushTestResult)
def send_test(db=Depends(get_db), user: dict = Depends(get_current_user)):
    """Push a test notification to the caller's own active subscriptions."""
    _require_settings()
    active = db.execute(
        "SELECT 1 FROM push_subscriptions WHERE user_id = ? AND is_active = 1 LIMIT 1",
        (user["id"],),
    ).fetchone()
    if active is None:
        raise HTTPException(409, "no active push subscription for this user")
    return PushTestResult(**push.push_to_user(db, user["id"], push.test_payload(user)))
