"""Tests for user profile modeling and persistence."""


from remedy.memory.profile import UserProfile


class TestUserProfile:
    def test_default_profile(self):
        profile = UserProfile()
        assert profile.user_id == "default"
        assert profile.traits == {}
        assert profile.facts == []
        assert profile.stats["sessions_count"] == 0

    def test_get_set_trait(self):
        profile = UserProfile()
        profile.set_trait("timezone", "UTC")
        assert profile.get_trait("timezone") == "UTC"
        assert profile.get_trait("nonexistent") is None
        assert profile.get_trait("nonexistent", "fallback") == "fallback"

    def test_set_trait_updates_existing(self):
        profile = UserProfile()
        profile.set_trait("language", "Python")
        profile.set_trait("language", "Rust", confidence=0.9)
        t = profile.traits["language"]
        assert t.value == "Rust"
        assert t.confidence == 0.9
        assert t.observation_count == 2

    def test_add_fact(self):
        profile = UserProfile()
        f1 = profile.add_fact("Works at Acme Corp", category="work")
        f2 = profile.add_fact("Likes sushi", category="personal")
        assert len(profile.facts) == 2
        assert f1.fact == "Works at Acme Corp"
        assert f1.category == "work"

    def test_record_session(self):
        profile = UserProfile()
        profile.record_session(30.0)
        assert profile.stats["sessions_count"] == 1
        assert profile.stats["avg_session_duration_minutes"] == 30.0

        profile.record_session(60.0)
        assert profile.stats["sessions_count"] == 2
        assert profile.stats["avg_session_duration_minutes"] == 45.0

    def test_record_skill_use(self):
        profile = UserProfile()
        profile.record_skill_use("memory-backup")
        profile.record_skill_use("memory-backup")
        profile.record_skill_use("git-commit")
        assert profile.stats["skills_used"]["memory-backup"] == 2
        assert profile.stats["skills_used"]["git-commit"] == 1
