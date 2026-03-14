"""
Tests for head/session_router.py
"""

import os
import pytest
from head.session_router import SessionRouter, Session


@pytest.fixture
def router(tmp_path):
    """Create a SessionRouter with a temp database."""
    db_path = str(tmp_path / "test_sessions.db")
    return SessionRouter(db_path=db_path)


class TestRegisterAndResolve:
    def test_register_and_resolve(self, router):
        router.register(
            channel_id="discord:100",
            machine_id="gpu-1",
            path="/home/user/project",
            daemon_session_id="sess-001",
            mode="auto",
        )
        session = router.resolve("discord:100")
        assert session is not None
        assert session.channel_id == "discord:100"
        assert session.machine_id == "gpu-1"
        assert session.path == "/home/user/project"
        assert session.daemon_session_id == "sess-001"
        assert session.status == "active"
        assert session.mode == "auto"
        assert session.sdk_session_id is None

    def test_resolve_nonexistent(self, router):
        result = router.resolve("discord:999")
        assert result is None

    def test_resolve_only_active(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")
        result = router.resolve("discord:100")
        assert result is None

    def test_register_auto_detaches_existing(self, router):
        # Register first session
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        session1 = router.resolve("discord:100")
        assert session1 is not None
        assert session1.daemon_session_id == "sess-001"

        # Register second session on same channel
        router.register("discord:100", "gpu-2", "/path2", "sess-002")
        session2 = router.resolve("discord:100")
        assert session2 is not None
        assert session2.daemon_session_id == "sess-002"
        assert session2.machine_id == "gpu-2"

        # First session should be in session_log (detached)
        found = router.find_session_by_daemon_id("sess-001")
        assert found is not None
        assert found.status == "detached"

    def test_register_multiple_channels(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.register("discord:200", "gpu-1", "/path2", "sess-002")

        s1 = router.resolve("discord:100")
        s2 = router.resolve("discord:200")
        assert s1 is not None
        assert s2 is not None
        assert s1.daemon_session_id == "sess-001"
        assert s2.daemon_session_id == "sess-002"

    def test_register_returns_name(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        assert isinstance(name, str)
        assert "-" in name  # Two words separated by hyphen

    def test_register_assigns_name_to_session(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        session = router.resolve("discord:100")
        assert session is not None
        assert session.name == name

    def test_register_generates_unique_names(self, router):
        name1 = router.register("discord:100", "gpu-1", "/path1", "sess-001")
        name2 = router.register("discord:200", "gpu-1", "/path2", "sess-002")
        assert name1 != name2


class TestUpdateSdkSession:
    def test_update_sdk_session(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.update_sdk_session("discord:100", "sdk-abc-123")

        session = router.resolve("discord:100")
        assert session is not None
        assert session.sdk_session_id == "sdk-abc-123"

    def test_update_sdk_session_nonexistent(self, router):
        # Should not raise, just no-op
        router.update_sdk_session("discord:999", "sdk-abc-123")


class TestUpdateMode:
    def test_update_mode(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001", mode="auto")
        router.update_mode("discord:100", "code")

        session = router.resolve("discord:100")
        assert session is not None
        assert session.mode == "code"

    def test_update_mode_nonexistent(self, router):
        # Should not raise, just no-op
        router.update_mode("discord:999", "plan")


class TestDetach:
    def test_detach_active_session(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        session = router.detach("discord:100")

        assert session is not None
        assert session.daemon_session_id == "sess-001"

        # Should no longer resolve as active
        assert router.resolve("discord:100") is None

    def test_detach_nonexistent(self, router):
        result = router.detach("discord:999")
        assert result is None

    def test_detach_moves_to_session_log(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")

        # Should be findable in session_log via find_session_by_daemon_id
        found = router.find_session_by_daemon_id("sess-001")
        assert found is not None
        assert found.status == "detached"

    def test_detach_already_detached(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")
        # Second detach should return None (no active session)
        result = router.detach("discord:100")
        assert result is None

    def test_detach_preserves_name_in_log(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")

        found = router.find_session_by_daemon_id("sess-001")
        assert found is not None
        assert found.name == name


class TestDestroy:
    def test_destroy_session(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        session = router.destroy("discord:100")

        assert session is not None
        assert session.daemon_session_id == "sess-001"

        # Should no longer resolve as active
        assert router.resolve("discord:100") is None

    def test_destroy_nonexistent(self, router):
        result = router.destroy("discord:999")
        assert result is None

    def test_destroy_marks_status(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.destroy("discord:100")

        # list_sessions should still show it as destroyed
        sessions = router.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].status == "destroyed"


class TestListSessions:
    def test_list_all(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.register("discord:200", "gpu-2", "/path2", "sess-002")

        sessions = router.list_sessions()
        assert len(sessions) == 2

    def test_list_filtered_by_machine(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.register("discord:200", "gpu-2", "/path2", "sess-002")

        sessions = router.list_sessions(machine_id="gpu-1")
        assert len(sessions) == 1
        assert sessions[0].machine_id == "gpu-1"

    def test_list_empty(self, router):
        sessions = router.list_sessions()
        assert sessions == []

    def test_list_includes_all_statuses(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.register("discord:200", "gpu-1", "/path2", "sess-002")
        router.destroy("discord:200")

        sessions = router.list_sessions(machine_id="gpu-1")
        assert len(sessions) == 2
        statuses = {s.status for s in sessions}
        assert "active" in statuses
        assert "destroyed" in statuses


class TestListActiveSessions:
    def test_only_active(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.register("discord:200", "gpu-1", "/path2", "sess-002")
        router.detach("discord:200")

        active = router.list_active_sessions()
        assert len(active) == 1
        assert active[0].daemon_session_id == "sess-001"

    def test_empty(self, router):
        assert router.list_active_sessions() == []

    def test_destroyed_not_included(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.destroy("discord:100")

        active = router.list_active_sessions()
        assert len(active) == 0


class TestFindSessionByDaemonId:
    def test_find_in_active(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        found = router.find_session_by_daemon_id("sess-001")
        assert found is not None
        assert found.daemon_session_id == "sess-001"
        assert found.status == "active"

    def test_find_in_session_log(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")

        found = router.find_session_by_daemon_id("sess-001")
        assert found is not None
        assert found.status == "detached"

    def test_not_found(self, router):
        result = router.find_session_by_daemon_id("nonexistent")
        assert result is None

    def test_find_preserves_mode(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001", mode="plan")
        router.detach("discord:100")

        found = router.find_session_by_daemon_id("sess-001")
        assert found is not None
        assert found.mode == "plan"

    def test_find_preserves_name(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        found = router.find_session_by_daemon_id("sess-001")
        assert found is not None
        assert found.name == name


class TestFindSessionsByMachinePath:
    def test_find_matching(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.register("discord:200", "gpu-1", "/path1", "sess-002")
        router.register("discord:300", "gpu-1", "/path2", "sess-003")

        results = router.find_sessions_by_machine_path("gpu-1", "/path1")
        assert len(results) == 2

    def test_find_no_match(self, router):
        router.register("discord:100", "gpu-1", "/path1", "sess-001")
        results = router.find_sessions_by_machine_path("gpu-2", "/path1")
        assert results == []

    def test_find_empty_db(self, router):
        results = router.find_sessions_by_machine_path("gpu-1", "/path")
        assert results == []

    def test_find_includes_all_statuses(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.register("discord:200", "gpu-1", "/path", "sess-002")
        router.destroy("discord:200")

        results = router.find_sessions_by_machine_path("gpu-1", "/path")
        assert len(results) == 2


class TestRenameSession:
    def test_rename_active_session(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        result = router.rename_session("discord:100", "my-project")
        assert result is True

        session = router.resolve("discord:100")
        assert session is not None
        assert session.name == "my-project"

    def test_rename_no_active_session(self, router):
        result = router.rename_session("discord:999", "my-project")
        assert result is False

    def test_rename_duplicate_name_fails(self, router):
        name1 = router.register("discord:100", "gpu-1", "/path1", "sess-001")
        router.register("discord:200", "gpu-1", "/path2", "sess-002")

        # Try to rename second session to first session's name
        result = router.rename_session("discord:200", name1)
        assert result is False

    def test_rename_same_name_succeeds(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        # Renaming to the same name should succeed
        result = router.rename_session("discord:100", name)
        assert result is True

    def test_rename_updates_name(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.rename_session("discord:100", "new-name")

        session = router.resolve("discord:100")
        assert session.name == "new-name"


class TestFindSessionByName:
    def test_find_active_by_name(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        found = router.find_session_by_name(name)
        assert found is not None
        assert found.daemon_session_id == "sess-001"
        assert found.name == name

    def test_find_detached_by_name(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")

        found = router.find_session_by_name(name)
        assert found is not None
        assert found.status == "detached"
        assert found.name == name

    def test_find_by_name_not_found(self, router):
        result = router.find_session_by_name("nonexistent-name")
        assert result is None

    def test_find_by_custom_name(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.rename_session("discord:100", "my-project")

        found = router.find_session_by_name("my-project")
        assert found is not None
        assert found.daemon_session_id == "sess-001"


class TestFindSessionByNameOrId:
    def test_find_by_name(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        found = router.find_session_by_name_or_id(name)
        assert found is not None
        assert found.daemon_session_id == "sess-001"

    def test_find_by_daemon_id(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        found = router.find_session_by_name_or_id("sess-001")
        assert found is not None
        assert found.daemon_session_id == "sess-001"

    def test_name_takes_precedence(self, router):
        """If a name matches, it should be returned even if the string also looks like an ID."""
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        found = router.find_session_by_name_or_id(name)
        assert found is not None
        assert found.name == name

    def test_not_found(self, router):
        result = router.find_session_by_name_or_id("nonexistent")
        assert result is None

    def test_find_detached_by_name(self, router):
        name = router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")

        found = router.find_session_by_name_or_id(name)
        assert found is not None
        assert found.status == "detached"

    def test_find_detached_by_id(self, router):
        router.register("discord:100", "gpu-1", "/path", "sess-001")
        router.detach("discord:100")

        found = router.find_session_by_name_or_id("sess-001")
        assert found is not None
        assert found.status == "detached"


class TestSchemaMigration:
    def test_existing_db_without_name_column(self, tmp_path):
        """Test that old databases without 'name' column get migrated."""
        import sqlite3
        db_path = str(tmp_path / "old.db")

        # Create old-schema DB
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE sessions (
                channel_id TEXT PRIMARY KEY,
                machine_id TEXT NOT NULL,
                path TEXT NOT NULL,
                daemon_session_id TEXT NOT NULL,
                sdk_session_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                mode TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE session_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                machine_id TEXT NOT NULL,
                path TEXT NOT NULL,
                daemon_session_id TEXT NOT NULL,
                sdk_session_id TEXT,
                mode TEXT,
                created_at TEXT NOT NULL,
                detached_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, 'active', 'auto', '2024-01-01', '2024-01-01')",
            ("discord:100", "gpu-1", "/path", "sess-old"),
        )
        conn.commit()
        conn.close()

        # Open with SessionRouter (should migrate)
        router = SessionRouter(db_path=db_path)

        # Old session should be readable
        session = router.resolve("discord:100")
        assert session is not None
        assert session.daemon_session_id == "sess-old"
        assert session.name is None  # No name in old data

        # New sessions should get names
        name = router.register("discord:200", "gpu-2", "/path2", "sess-new")
        assert name is not None
        assert "-" in name
