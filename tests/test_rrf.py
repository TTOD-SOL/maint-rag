from rag.retrieve import rrf_merge


def test_doc_in_both_lists_wins():
    merged = rrf_merge([["a", "b", "c"], ["c", "d", "a"]], k=60)
    ids = [i for i, _ in merged]
    assert ids[0] == "a"  # rank 1 + rank 3 beats c (rank 3 + rank 1) on tie-break: earlier first appearance
    assert set(ids) == {"a", "b", "c", "d"}


def test_scores_follow_formula():
    merged = dict(rrf_merge([["x", "y"], ["y"]], k=60))
    assert merged["x"] == 1 / 61
    assert merged["y"] == 1 / 62 + 1 / 61


def test_top_n_and_order_descending():
    merged = rrf_merge([["a", "b", "c", "d"], ["d", "c", "b", "a"]], k=1, top_n=2)
    assert len(merged) == 2
    assert merged[0][1] >= merged[1][1]


def test_duplicates_within_list_ignored():
    merged = dict(rrf_merge([["a", "a", "b"]], k=60))
    assert merged["a"] == 1 / 61
    assert merged["b"] == 1 / 63


def test_empty_inputs():
    assert rrf_merge([]) == []
    assert rrf_merge([[], []]) == []


def test_deterministic():
    lists = [["a", "b", "c"], ["b", "a", "d"]]
    assert rrf_merge(lists) == rrf_merge(lists)
