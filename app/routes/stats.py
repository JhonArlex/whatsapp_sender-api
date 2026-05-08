"""Routes para estadísticas del dashboard."""

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.db import query

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("/overview")
def overview(user: dict = Depends(get_current_user)):
    user_id = str(user["id"])
    total_jobs = query("SELECT COUNT(*) as c FROM jobs WHERE user_id = %s", (user_id,))[0]["c"]
    running_jobs = query("SELECT COUNT(*) as c FROM jobs WHERE user_id = %s AND status = 'running'", (user_id,))[0]["c"]
    total_sent = query("SELECT COUNT(*) as c FROM message_history WHERE user_id = %s AND status = 'sent'", (user_id,))[0]["c"]
    total_failed = query("SELECT COUNT(*) as c FROM message_history WHERE user_id = %s AND status = 'failed'", (user_id,))[0]["c"]
    total_groups = query(
        "SELECT COUNT(*) as c FROM groups_cache gc "
        "JOIN instances_cache ic ON ic.id = gc.instance_cache_id "
        "JOIN evolution_connections ec ON ec.id = ic.connection_id "
        "WHERE ec.user_id = %s",
        (user_id,),
    )[0]["c"]
    connections_count = query(
        "SELECT COUNT(*) as c FROM evolution_connections WHERE user_id = %s",
        (user_id,),
    )[0]["c"]

    return {
        "total_jobs": total_jobs,
        "running_jobs": running_jobs,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "total_groups_cached": total_groups,
        "connections_count": connections_count,
    }


@router.get("/daily")
def daily_stats(user: dict = Depends(get_current_user)):
    rows = query(
        """SELECT DATE(sent_at) as day, status, COUNT(*) as count
           FROM message_history
           WHERE user_id = %s AND sent_at >= NOW() - INTERVAL '30 days'
           GROUP BY DATE(sent_at), status
           ORDER BY day ASC""",
        (str(user["id"]),),
    )
    return {"daily": [{"day": str(r["day"]), "status": r["status"], "count": r["count"]} for r in rows]}
