# tests/test_evaluator_3card.py v1.0
"""
Unit-тесты для модуля ofc_evaluator_3card.py.
"""

import pytest

# Импорты из тестируемого модуля
try:
    from ofc_evaluator_3card import evaluate_3_card_ofc, three_card_lookup
except ImportError:
    pytest.skip("Skipping 3-card evaluator tests because module could not be imported", allow_module_level=True)

# Импорты из ofc_logic для создания карт
try:
    from ofc_logic import Card, INVALID_CARD
except ImportError:
    pytest.skip("Skipping 3-card evaluator tests because ofc_logic could not be imported", allow_module_level=True)


# --- Хелперы ---
def hand_to_int(card_strs: list) -> tuple:
    """Конвертирует список строк в кортеж int карт."""
    ints = []
    for s in card_strs:
        if s is None: raise ValueError("None not allowed in 3-card hand")
        try: ints.append(Card.from_str(s))
        except ValueError: raise ValueError(f"Invalid card string: {s}")
    if len(ints) != 3: raise ValueError("Hand must contain 3 cards")
    return tuple(ints)

# --- Тесты валидных рук ---
@pytest.mark.parametrize("hand_str, expected_rank, expected_type, expected_rank_str", [
    # Trips
    (['Ah', 'Ad', 'As'], 1, 'Trips', 'AAA'),
    (['Kh', 'Kd', 'Kc'], 2, 'Trips', 'KKK'),
    (['2h', '2d', '2c'], 13, 'Trips', '222'),
    # Pairs
    (['Ah', 'Ad', 'Ks'], 14, 'Pair', 'AAK'),
    (['Ah', 'Ad', '2s'], 25, 'Pair', 'AA2'),
    (['Qh', 'Qs', 'Jd'], 40, 'Pair', 'QQJ'),
    (['Qh', 'Qs', '2d'], 49, 'Pair', 'QQ2'),
    (['6h', '6d', 'Ac'], 110, 'Pair', '66A'),
    (['6h', '6d', '5c'], 118, 'Pair', '665'),
    (['2h', '2d', 'Ac'], 158, 'Pair', '22A'),
    (['2h', '2d', '3c'], 169, 'Pair', '223'),
    # High Card
    (['Ah', 'Kh', 'Qd'], 170, 'High Card', 'AKQ'),
    (['Ah', 'Kh', '2d'], 180, 'High Card', 'AK2'),
    (['Kh', 'Qh', 'Jd'], 236, 'High Card', 'KQJ'),
    (['Kh', 'Qh', '2d'], 245, 'High Card', 'KQ2'),
    (['5h', '3d', '2c'], 455, 'High Card', '532'), # Worst High Card
])
def test_evaluate_3_card_valid(hand_str, expected_rank, expected_type, expected_rank_str):
    """Тестирует оценку различных валидных 3-карточных рук."""
    card_ints = hand_to_int(hand_str)
    rank, type_str, rank_str_out = evaluate_3_card_ofc(card_ints[0], card_ints[1], card_ints[2])
    assert rank == expected_rank
    assert type_str == expected_type
    assert rank_str_out == expected_rank_str

# --- Тесты невалидных входов ---
def test_evaluate_3_card_invalid_input():
    """Тестирует ошибки при невалидном входе."""
    c1 = Card.from_str('As')
    c2 = Card.from_str('Ks')
    # Неверный тип
    with pytest.raises(TypeError): evaluate_3_card_ofc(c1, c2, "Qd") # type: ignore
    with pytest.raises(TypeError): evaluate_3_card_ofc(c1, None, c2) # type: ignore
    # Невалидное значение
    with pytest.raises(ValueError): evaluate_3_card_ofc(c1, c2, INVALID_CARD)
    with pytest.raises(ValueError): evaluate_3_card_ofc(c1, c2, 0)
    # Дубликаты
    with pytest.raises(ValueError): evaluate_3_card_ofc(c1, c1, c2)

def test_lookup_table_completeness():
    """Проверяет, что все 455 комбинаций рангов присутствуют в таблице."""
    assert len(three_card_lookup) == 455, "Lookup table size mismatch"
    # Проверяем наличие ключей для лучших и худших рук
    assert (12, 12, 12) in three_card_lookup # AAA
    assert (2, 1, 0) in three_card_lookup # 432
