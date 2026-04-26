from __future__ import annotations

from datetime import date

import pytest

from scout.dedup import DedupCache


@pytest.fixture
def cache(tmp_path):
    c = DedupCache.open(tmp_path / "seen.sqlite")
    yield c
    c.close()


def test_id_round_trip(cache):
    assert not cache.has_id("greenhouse:foo:1")
    cache.add("greenhouse:foo:1", company="Foo Inc", role="Senior Engineer", discovered_at=date.today())
    cache.commit()
    assert cache.has_id("greenhouse:foo:1")


def test_fuzzy_collision_within_distance(cache):
    cache.add("a", company="Acme", role="Senior Backend Engineer", discovered_at=date.today())
    cache.commit()
    # role differs by 1 char → distance 1, should collide
    assert cache.fuzzy_collision("Acme", "Senior Backend Engineers") == "a"


def test_fuzzy_no_collision_when_company_differs(cache):
    cache.add("a", company="Acme", role="Senior Engineer", discovered_at=date.today())
    cache.commit()
    assert cache.fuzzy_collision("Globex", "Senior Engineer") is None


def test_fuzzy_no_collision_when_role_too_different(cache):
    cache.add("a", company="Acme", role="Senior Backend Engineer", discovered_at=date.today())
    cache.commit()
    assert cache.fuzzy_collision("Acme", "Junior iOS Developer") is None


def test_add_many_ids_dedups_silently(cache):
    cache.add_many_ids(["x", "y", "z", "x"])
    cache.commit()
    assert cache.has_id("x")
    assert cache.has_id("z")
