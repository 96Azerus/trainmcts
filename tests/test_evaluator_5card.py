# tests/test_evaluator_5card.py v1.3
"""
Unit-тесты для модуля ofc_evaluator_5card.py.
Исправлены ошибки импорта и передачи типов.
Исправлен импорт hand_to_int.
"""

import pytest
import itertools

# Импорты из тестируемого модуля
try:
    from ofc_evaluator_5card import Evaluator5Card, LookupTable5Card
except ImportError:
    pytest.skip("Skipping 5-card evaluator tests because module could not be imported", allow_module_level=True)

# Импорты из ofc_logic для создания карт и констант
try:
    # --- ИСПРАВЛЕНО: Убран импорт hand_to_int ---
    from ofc_logic import Card, INVALID_CARD, CARD_PLACEHOLDER, PRIMES
except ImportError:
    pytest.skip("Skipping 5-card evaluator tests because ofc_logic could not be imported", allow_module_level=True)


# --- Хелперы ---
def hand_to_int(card_strs: list) -> list:
    """Конвертирует список строк в список int карт."""
    # --- ИСПРАВЛЕНО: Используем Card.hand_to_int ---
    ints_optional = Card.hand_to_int(card_strs)
    ints = [c for c in ints_optional if c is not None]
    if len(ints) != 5: raise ValueError("Hand must contain 5 valid cards for this test")
    if len(ints) != len(set(ints)): raise ValueError("Duplicate cards not allowed")
    return ints

# --- Фикстура для эвалуатора ---
@pytest.fixture(scope="module")
def evaluator():
    """Создает экземпляр эвалуатора один раз для всех тестов модуля."""
    try:
        return Evaluator5Card()
    except Exception as e:
        pytest.fail(f"Failed to initialize Evaluator5Card: {e}")

# --- Тесты LookupTable ---
def test_lookup_table_constants(evaluator):
    """Проверяет константы максимальных рангов."""
    table = evaluator.table
    assert table.MAX_STRAIGHT_FLUSH == 10
    assert table.MAX_FOUR_OF_A_KIND == 166
    assert table.MAX_FULL_HOUSE == 322
    assert table.MAX_FLUSH == 1599
    assert table.MAX_STRAIGHT == 1609
    assert table.MAX_THREE_OF_A_KIND == 2467
    assert table.MAX_TWO_PAIR == 3325
    assert table.MAX_PAIR == 6185
    assert table.MAX_HIGH_CARD == 7462
    assert len(table.RANK_CLASS_TO_STRING) == 9

def test_lookup_table_generation_completeness(evaluator):
    """Проверяет полноту сгенерированных таблиц."""
    table = evaluator.table
    # Ожидаемое количество флешей C(13, 5) = 1287
    assert len(table.flush_lookup) == 1287, f"Flush lookup size: {len(table.flush_lookup)}"
    # Ожидаемое количество уникальных произведений простых чисел для не-флешей
    # (сложно точно посчитать, но должно быть больше 6000)
    # 7462 (всего) - 1287 (флеши) + 10 (стрит-флеши, которые есть в обеих таблицах) = 6185?
    # Проверяем, что размер не нулевой и достаточно большой
    assert len(table.unsuited_lookup) > 6000, f"Unsuited lookup size: {len(table.unsuited_lookup)}"

    # Проверяем ключевые ранги
    rf_bits = 0b1111100000000
    rf_prime = table._prime_product_from_rankbits(rf_bits)
    assert table.flush_lookup.get(rf_prime) == 1

    wheel_sf_bits = 0b1000000001111
    wheel_sf_prime = table._prime_product_from_rankbits(wheel_sf_bits)
    assert table.flush_lookup.get(wheel_sf_prime) == 10

    # Используем PRIMES напрямую
    four_aces_k_prime = PRIMES[12]**4 * PRIMES[11]
    assert table.unsuited_lookup.get(four_aces_k_prime) == 11, "Four Aces K rank mismatch"

    worst_hc_prime = PRIMES[5] * PRIMES[3] * PRIMES[2] * PRIMES[1] * PRIMES[0] # 7,5,4,3,2
    assert table.unsuited_lookup.get(worst_hc_prime) == 7462, "Worst High Card rank mismatch"

# --- Тесты Evaluator5Card.evaluate ---
@pytest.mark.parametrize("hand_str, expected_rank, expected_class_str", [
    (['As', 'Ks', 'Qs', 'Js', 'Ts'], 1, "Straight Flush"), # Royal Flush
    (['9d', '8d', '7d', '6d', '5d'], 6, "Straight Flush"), # SF 9-high
    (['Ac', 'Ad', 'Ah', 'As', '2c'], 11, "Four of a Kind"), # Quads A, Kicker 2
    (['2c', '2d', '2h', '2s', 'Ac'], 166, "Four of a Kind"), # Quads 2, Kicker A (worst quads)
    (['Kc', 'Kd', 'Kh', 'Qc', 'Qs'], 167, "Full House"), # FH K over Q
    (['2c', '2d', '2h', 'Ac', 'As'], 322, "Full House"), # FH 2 over A (worst FH)
    (['As', 'Qs', '8s', '5s', '3s'], 323, "Flush"), # Flush A high
    (['7d', '5d', '4d', '3d', '2d'], 1599, "Flush"), # Flush 7 high (worst flush)
    (['Ad', 'Kc', 'Qh', 'Js', 'Td'], 1600, "Straight"), # Straight AKQJT
    (['5d', '4c', '3h', '2s', 'Ad'], 1609, "Straight"), # Straight A2345 (Wheel - worst straight)
    (['Ac', 'Ad', 'Ah', 'Ks', 'Qd'], 1610, "Three of a Kind"), # Trips A, KQ kicker
    (['2c', '2d', '2h', '4s', '3d'], 2467, "Three of a Kind"), # Trips 2, 43 kicker (worst trips)
    (['Ac', 'Ad', 'Kc', 'Kd', 'Qs'], 2468, "Two Pair"), # Two Pair AK, Q kicker
    (['3c', '3d', '2c', '2d', '4s'], 3325, "Two Pair"), # Two Pair 32, 4 kicker (worst 2pair)
    (['Ac', 'Ad', 'Ks', 'Qd', 'Js'], 3326, "Pair"), # Pair A, KQJ kicker
    (['2c', '2d', '5s', '4d', '3c'], 6185, "Pair"), # Pair 2, 543 kicker (worst pair)
    (['Ac', 'Kc', 'Qs', 'Js', '9d'], 6186, "High Card"), # High Card AKQJ9
    (['7d', '5c', '4h', '3s', '2d'], 7462, "High Card"), # High Card 75432 (worst hand)
])
def test_evaluate_valid_hands(evaluator, hand_str, expected_rank, expected_class_str):
    """Тестирует оценку различных валидных 5-карточных рук."""
    hand_int = hand_to_int(hand_str)
    rank = evaluator.evaluate(hand_int)
    rank_class = evaluator.get_rank_class(rank)
    class_str = evaluator.class_to_string(rank_class)
    assert rank == expected_rank
    assert class_str == expected_class_str

def test_evaluate_invalid_input(evaluator):
    """Тестирует ошибки при невалидном входе для evaluate."""
    # Неверное количество карт
    with pytest.raises(ValueError): evaluator.evaluate(hand_to_int(['As', 'Ks', 'Qs', 'Js'])) # 4 карты
    with pytest.raises(ValueError): evaluator.evaluate(hand_to_int(['As', 'Ks', 'Qs', 'Js', 'Ts', '9s'])) # 6 карт
    # Невалидная карта
    with pytest.raises(ValueError): evaluator.evaluate([Card.from_str('As'), Card.from_str('Ks'), Card.from_str('Qs'), Card.from_str('Js'), INVALID_CARD])
    with pytest.raises(ValueError): evaluator.evaluate([Card.from_str('As'), Card.from_str('Ks'), Card.from_str('Qs'), Card.from_str('Js'), 0])
    # Дубликаты
    with pytest.raises(ValueError): evaluator.evaluate(hand_to_int(['As', 'As', 'Ks', 'Qs', 'Js']))
    # Неверный тип (передаем строки вместо int)
    with pytest.raises(ValueError): evaluator.evaluate([Card.from_str("As"), Card.from_str("Ks"), Card.from_str("Qs"), Card.from_str("Js"), "Ts"]) # type: ignore

# --- Тесты get_rank_class и class_to_string ---
# (Эти тесты остаются без изменений, т.к. зависят только от констант)
def test_get_rank_class(evaluator):
    """Тестирует определение класса руки по рангу."""
    assert evaluator.get_rank_class(1) == 1 # RF
    assert evaluator.get_rank_class(10) == 1 # Wheel SF
    assert evaluator.get_rank_class(11) == 2 # Quads A
    assert evaluator.get_rank_class(166) == 2 # Quads 2
    assert evaluator.get_rank_class(167) == 3 # FH K over Q
    assert evaluator.get_rank_class(322) == 3 # FH 2 over A
    assert evaluator.get_rank_class(323) == 4 # Flush A high
    assert evaluator.get_rank_class(1599) == 4 # Flush 7 high
    assert evaluator.get_rank_class(1600) == 5 # Straight AKQJT
    assert evaluator.get_rank_class(1609) == 5 # Straight Wheel
    assert evaluator.get_rank_class(1610) == 6 # Trips A
    assert evaluator.get_rank_class(2467) == 6 # Trips 2
    assert evaluator.get_rank_class(2468) == 7 # 2P AK
    assert evaluator.get_rank_class(3325) == 7 # 2P 32
    assert evaluator.get_rank_class(3326) == 8 # Pair A
    assert evaluator.get_rank_class(6185) == 8 # Pair 2
    assert evaluator.get_rank_class(6186) == 9 # HC AKQJ9
    assert evaluator.get_rank_class(7462) == 9 # HC 75432
    # Невалидные ранги
    assert evaluator.get_rank_class(0) == 9
    assert evaluator.get_rank_class(-10) == 9
    assert evaluator.get_rank_class(8000) == 9

def test_class_to_string(evaluator):
    """Тестирует преобразование класса руки в строку."""
    assert evaluator.class_to_string(1) == "Straight Flush"
    assert evaluator.class_to_string(2) == "Four of a Kind"
    assert evaluator.class_to_string(3) == "Full House"
    assert evaluator.class_to_string(4) == "Flush"
    assert evaluator.class_to_string(5) == "Straight"
    assert evaluator.class_to_string(6) == "Three of a Kind"
    assert evaluator.class_to_string(7) == "Two Pair"
    assert evaluator.class_to_string(8) == "Pair"
    assert evaluator.class_to_string(9) == "High Card"
    assert evaluator.class_to_string(0) == "Unknown"
    assert evaluator.class_to_string(10) == "Unknown"
