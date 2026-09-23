from src.dedupe import unique_preserve_order


def test_removes_duplicates():
    assert unique_preserve_order([1, 2, 2, 3, 1]) == [1, 2, 3]


def test_preserves_order():
    assert unique_preserve_order([3, 1, 2, 1, 3]) == [3, 1, 2]


def test_empty_list():
    assert unique_preserve_order([]) == []
