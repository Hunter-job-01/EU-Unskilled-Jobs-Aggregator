from src.dedup import (canonical_url, description_fingerprint,
                       find_duplicate_groups, strong_identity_keys)

DESC = "Warehouse work in a modern logistics centre. " * 20


def test_canonical_strips_tracking_keeps_identity_params():
    u = canonical_url("https://Boards.example.com/jobs/123/?utm_source=nl&id=123#x")
    assert u == "https://boards.example.com/jobs/123?id=123"
    # identity-bearing params survive
    assert "id=123" in u
    u2 = canonical_url("https://example.com/apply?jobId=9&ref=partner")
    assert "jobId=9" in u2 and "ref=partner" in u2


def test_canonical_www_and_trailing_slash():
    assert canonical_url("https://www.example.com/a/") == "https://example.com/a"


def test_short_descriptions_not_hashed():
    assert description_fingerprint("too short") == ""
    assert description_fingerprint(DESC)


def test_strong_url_identity_groups():
    jobs = [
        {"uid": "a", "source": "eures", "url": "https://x.example/j/1?utm_campaign=z",
         "title": "Packer", "company": "Acme", "country": "PL", "description": DESC},
        {"uid": "b", "source": "arbeitnow", "url": "https://x.example/j/1",
         "title": "Packer", "company": "Acme", "country": "PL", "description": DESC},
    ]
    groups = find_duplicate_groups(jobs)
    assert len(groups["corroborated"]) == 1
    assert {m["uid"] for m in groups["corroborated"][0]["members"]} == {"a", "b"}


def test_weak_signal_never_corroborates():
    # same normalised title+company+country, different URLs and text
    jobs = [
        {"uid": "a", "source": "eures", "url": "https://x.example/j/1",
         "title": "Packer", "company": "Acme Sp. z o.o.", "country": "PL",
         "description": DESC + "1"},
        {"uid": "b", "source": "arbeitnow", "url": "https://y.example/offer/9",
         "title": "packer", "company": "Acme sp z oo", "country": "PL",
         "description": DESC + "2"},
    ]
    groups = find_duplicate_groups(jobs)
    assert groups["corroborated"] == []
    assert len(groups["possible"]) == 1
    assert "review only" in groups["possible"][0]["note"]


def test_description_hash_needs_same_employer_and_country():
    jobs = [
        {"uid": "a", "source": "s1", "url": "", "title": "Packer",
         "company": "Acme", "country": "PL", "description": DESC},
        {"uid": "b", "source": "s2", "url": "", "title": "Packer",
         "company": "OtherCorp", "country": "PL", "description": DESC},
    ]
    keys_a = strong_identity_keys(jobs[0])
    keys_b = strong_identity_keys(jobs[1])
    assert not (keys_a & keys_b)


def test_no_keys_no_groups():
    jobs = [{"uid": "a", "source": "s", "url": "", "title": "x",
             "company": "", "country": "PL", "description": ""}]
    assert find_duplicate_groups(jobs) == {"corroborated": [], "possible": []}
