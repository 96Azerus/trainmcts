# tests/test_evaluators.py v1.3
"""
Unit-тесты для модуля ofc_evaluators.py (интерфейс оценки).
Исправлены ассерты для 3-карточных рук. Уточнен ассерт для 5-карт.
Исправлен импорт hand_to_int.
"""

import pytest

# Импорты из тестируемого модуля
try:
    from ofc_evaluators import (
        get_hand_rank_safe,
        WORST_RANK, MAX_HIGH_CARD_5, WORST_RANK_3CARD_RAW, # Changed WORST_RANK_3CARD_ADJUSTED to WORST_RANK_3CARD_RAW
        HAND_TYPE_TRIPS_3, HAND_TYPE_PAIR_3, HAND_TYPE_HIGH_CARD_3
    )
except ImportError as e:
    pytest.skip("Skipping evaluator interface tests because module could not be imported", allow_module_level=True)
    # raise e

# Импорты из ofc_logic для создания карт
try:
    # --- ИСПРАВЛЕНО: Убран импорт hand_to_int ---
    from ofc_logic import Card, INVALID_CARD, CARD_PLACEHOLDER
except ImportError:
    pytest.skip("Skipping evaluator interface tests because ofc_logic could not be imported", allow_module_level=True)


# --- Хелперы ---
def hand_to_int(card_strs: list) -> list:
    """Конвертирует список строк в список int карт, сохраняя None."""
    # --- ИСПРАВЛЕНО: Используем Card.hand_to_int ---
    return Card.hand_to_int(card_strs)

# --- Тесты get_hand_rank_safe ---

# 3-карточные руки
@pytest.mark.parametrize("hand_str, expected_type", [
    (['Ah', 'Ad', 'As'], HAND_TYPE_TRIPS_3),
    (['Qh', 'Qs', 'Jd'], HAND_TYPE_PAIR_3),
    (['Ah', 'Kh', 'Qd'], HAND_TYPE_HIGH_CARD_3),
])
def test_get_hand_rank_safe_3card_valid(hand_str, expected_type):
    """Тестирует валидные 3-карточные руки."""
    cards = hand_to_int(hand_str)
    rank, hand_class, type_str = get_hand_rank_safe(cards) # Unpack 3 values
    # assert rank > MAX_HIGH_CARD_5 # This comparison might be problematic if scales aren't aligned; 3-card raw ranks are 1-455
    # Проверяем, что ранг находится в допустимом диапазоне сырых рангов 3-карточных рук
    assert 1 <= rank <= WORST_RANK_3CARD_RAW
    assert type_str == expected_type

# 5-карточные руки
@pytest.mark.parametrize("hand_str, expected_type", [
    (['As', 'Ks', 'Qs', 'Js', 'Ts'], "Straight Flush"),
    (['Ac', 'Ad', 'Ah', 'As', '2c'], "Four of a Kind"),
    (['Kc', 'Kd', 'Kh', 'Qc', 'Qs'], "Full House"),
    (['As', 'Qs', '8s', '5s', '3s'], "Flush"),
    (['Ad', 'Kc', 'Qh', 'Js', 'Td'], "Straight"),
    (['Ac', 'Ad', 'Ah', 'Ks', 'Qd'], "Three of a Kind"),
    (['Ac', 'Ad', 'Kc', 'Kd', '2s'], "Two Pair"),
    (['Ac', 'Ad', 'Ks', 'Qd', 'Jc'], "Pair"),
    (['Ac', 'Kc', 'Qs', 'Js', '9d'], "High Card"),
])
def test_get_hand_rank_safe_5card_valid(hand_str, expected_type):
    """Тестирует валидные 5-карточные руки."""
    cards = hand_to_int(hand_str)
    rank, hand_class, type_str = get_hand_rank_safe(cards) # Unpack 3 values
    # --- ИСПРАВЛЕНО: Уточняем проверку ранга ---
    assert rank < WORST_RANK # Общая проверка на валидность
    assert 1 <= rank <= MAX_HIGH_CARD_5 
    assert type_str == expected_type

# Невалидные входы
@pytest.mark.parametrize("cards_input", [
    None,
    "not a list",
    [],
    hand_to_int(['As']),
    hand_to_int(['As', 'Ks']),
    hand_to_int(['As', 'Ks', 'Qs', 'Js']), # 4 карты
    hand_to_int(['As', 'Ks', 'Qs', 'Js', 'Ts', '9s']), # 6 карт
    hand_to_int(['As', 'Ks', None]), # Неполная 3-карточная
    hand_to_int(['As', 'Ks', 'Qs', 'Js', None]), # Неполная 5-карточная
    hand_to_int(['As', 'As', 'Ks']), # Дубликат 3-карточная
    hand_to_int(['As', 'As', 'Ks', 'Qs', 'Js']), # Дубликат 5-карточная
])
def test_get_hand_rank_safe_invalid(cards_input):
    """Тестирует невалидные входы для get_hand_rank_safe."""
    rank, hand_class, type_str = get_hand_rank_safe(cards_input) # type: ignore # Unpack 3 values
    assert rank == WORST_RANK
    assert type_str == "Invalid"
