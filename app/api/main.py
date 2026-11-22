import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException
from sqlmodel import select

from app.agent.llm import LlamaGuard, OllamaLLM
from app.agent.session import Deps
from app.api.ws import router as ws_router
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.db import repo
from app.db.models import AuditLog, Booking, Conversation, Message
from app.providers.calendar import build_calendar
from app.providers.notify import NtfyNotifier
from app.security.auth import require_admin
from app.security.ratelimit import RateLimiter

log = logging.getLogger("app")


def create_app(st: Settings | None = None, deps: Deps | None = None, voice=None) -> FastAPI:
    st = st or get_settings()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if deps is None:
            db = repo.Database(st.database_url)
            app.state.deps = Deps(st, db, OllamaLLM(st), LlamaGuard(st), build_calendar(st),
                                  NtfyNotifier(st.ntfy_url, st.ntfy_topic), RateLimiter(st.rl_turns_per_min))
        else:
            app.state.deps = deps
        repo.ensure_owner(app.state.deps.db, st)
        app.state.connect_limiter = RateLimiter(st.rl_connect_per_min)
        if voice is not None:
            app.state.voice = voice or None  # voice=False forces text-only mode
        else:
            from app.voice import load_voice
            app.state.voice = load_voice(st)
        sched = AsyncIOScheduler()
        sched.add_job(lambda: log.info("housekeeping %s", repo.housekeeping(app.state.deps.db)), "interval", minutes=10)
        sched.start()
        yield
        sched.shutdown(wait=False)

    app = FastAPI(title="Gatekeeper", lifespan=lifespan)
    app.include_router(ws_router)

    @app.get("/healthz")
    async def healthz():
        d = app.state.deps
        llm_ok = await d.llm.alive() if hasattr(d.llm, "alive") else True
        return {"ok": True, "llm": llm_ok, "calendar": d.calendar.name, "voice": app.state.voice is not None,
                "tts": bool(app.state.voice and app.state.voice.tts), "ntfy": bool(st.ntfy_topic)}

    @app.get("/api/conversations", dependencies=[Depends(require_admin)])
    def conversations(limit: int = 50):
        with app.state.deps.db.session() as s:
            return s.exec(select(Conversation).order_by(Conversation.started_at.desc()).limit(min(limit, 200))).all()

    @app.get("/api/conversations/{cid}", dependencies=[Depends(require_admin)])
    def conversation(cid: str):
        with app.state.deps.db.session() as s:
            c = s.get(Conversation, cid)
            if not c:
                raise HTTPException(404)
            return {"conversation": c,
                    "messages": s.exec(select(Message).where(Message.conversation_id == cid).order_by(Message.id)).all(),
                    "bookings": s.exec(select(Booking).where(Booking.conversation_id == cid)).all(),
                    "audit": s.exec(select(AuditLog).where(AuditLog.conversation_id == cid).order_by(AuditLog.id)).all()}

    return app


def run():  # `python -m app.api.main`
    import uvicorn
    uvicorn.run("app.api.main:create_app", factory=True, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
