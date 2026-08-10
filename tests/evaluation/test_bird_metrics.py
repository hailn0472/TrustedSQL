from benchmark_eval.ex_metrics import ex_match, soft_f1


def test_bird_ex_uses_set_equality_and_ignores_row_order_and_duplicates() -> None:
    assert ex_match([[1], [2], [2]], [[2], [1]])
    assert not ex_match([[1], [3]], [[1], [2]])


def test_ex_handles_array_cells_without_treating_array_as_scalar_shape() -> None:
    predicted = [{"schedule_rooms": ["BE301"]}]
    expected = [{"room": "BE301"}]
    assert not ex_match(predicted, expected)
    assert soft_f1(predicted, expected) == 0.0


def test_ex_matches_equal_array_cells_after_recursive_canonicalization() -> None:
    predicted = [{"values": ["1", "2"]}]
    expected = [{"values": [1, 2]}]
    assert ex_match(predicted, expected)


def test_bird_soft_f1_matches_rows_by_position_and_values_by_membership() -> None:
    assert soft_f1([[1, 2]], [[2, 1]]) == 1.0
    assert soft_f1([[1, 9]], [[1, 2]]) == 0.5
    assert soft_f1([], []) == 1.0
    assert soft_f1([], [[1]]) == 0.0

