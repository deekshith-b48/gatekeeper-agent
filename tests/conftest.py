import pytest

from app.db import repo
from app.agent.session import CallSession
from tests.support import InMemoryCalendar, ScriptedLLM, make_deps


@pytest.fixture
def cal():
    return InMemoryCalendar()


@pytest.fixture
def make_session(tmp_path, cal):
    def _make(outputs=None, contact="caller-1", calendar=None):
        deps = make_deps(tmp_path, ScriptedLLM(outputs or [{"say": "ok"}]), calendar or cal)
        owner = repo.get_owner(deps.db, "owner")
        caller, conv = repo.start_conversation(deps.db, owner, contact)
        return CallSession(deps, owner, caller, conv.id), deps
    return _make
