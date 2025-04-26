# ofc_evaluators.py v1.0
"""
Интерфейс для оценки покерных комбинаций OFC (3 и 5 карт).
Использует специализированные модули для 3- и 5-карточных рук.
"""

import logging
from typing import List, Optional, Tuple

# Импортируем конкретные эвалюаторы
try:
    from ofc_evaluator_3card import evaluate_3_card_ofc
except ImportError:
    logging.critical("Failed to import evaluate_3_card_ofc from ofc_evaluator_3card.py")
    # Заглушка, чтобы код мог работать дальше с ошибкой
    def evaluate_3_card_ofc(c1, c2, c3) -> Tuple[int, str, str]: return (999, "Error", "ERR")

try:
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_5card import LookupTable5Card
except ImportError:
    logging.critical("Failed to import evaluator_5card_instance from ofc_evaluator_5card.py")
    # Заглушка
    class MockEvaluator5Card:
        WORST_RANK_5CARD = 9999
        def evaluate(self, cards): return self.WORST_RANK_5CARD
        def get_rank_class(self, rank): return 9
        def class_to_string(self, r_class): return "Error"
    evaluator_5card = MockEvaluator5Card()
    LookupTable5Card = MockEvaluator5Card # Используем заглушку и для констант

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)

# --- Константы Рангов (для удобства доступа) ---
# 5-карточные ранги (меньше = лучше)
RANK_CLASS_STRAIGHT_FLUSH: int = LookupTable5Card.MAX_STRAIGHT_FLUSH
RANK_CLASS_QUADS: int = LookupTable5Card.MAX_FOUR_OF_A_KIND
RANK_CLASS_FULL_HOUSE: int = LookupTable5Card.MAX_FULL_HOUSE
RANK_CLASS_FLUSH: int = LookupTable5Card.MAX_FLUSH
RANK_CLASS_STRAIGHT: int = LookupTable5Card.MAX_STRAIGHT
RANK_CLASS_TRIPS_5: int = LookupTable5Card.MAX_THREE_OF_A_KIND # Трипс в 5-карточной
RANK_CLASS_TWO_PAIR: int = LookupTable5Card.MAX_TWO_PAIR
RANK_CLASS_PAIR_5: int = LookupTable5Card.MAX_PAIR          # Пара в 5-карточной
RANK_CLASS_HIGH_CARD_5: int = LookupTable5Card.MAX_HIGH_CARD # Старшая карта в 5-карточной
WORST_RANK_5CARD: int = LookupTable5Card.WORST_RANK_5CARD

# 3-карточные типы (строки)
HAND_TYPE_TRIPS_3: str = "Trips"
HAND_TYPE_PAIR_3: str = "Pair"
HAND_TYPE_HIGH_CARD_3: str = "High Card"
WORST_RANK_3CARD: int = 456 # Ранг хуже худшей 3-карточной руки

# Общий худший ранг
WORST_RANK: int = max(WORST_RANK_3CARD, WORST_RANK_5CARD) + 1

# --- Основная функция оценки ---

def get_hand_rank_safe(cards: List[Optional[int]]) -> Tuple[int, str]:
    """
    Безопасно вычисляет ранг и тип руки (3 или 5 карт).
    Меньший ранг означает более сильную руку.

    Args:
        cards (List[Optional[int]]): Список карт (int) или None.

    Returns:
        Tuple[int, str]: (Ранг руки, Строка типа руки).
                         Для неполных/невалидных рук возвращает (WORST_RANK, "Invalid").
    """
    if not isinstance(cards, list):
        logger.warning(f"get_hand_rank_safe received non-list input: {type(cards)}")
        return WORST_RANK, "Invalid"

    # Импортируем Card здесь, чтобы избежать циклического импорта на уровне модуля
    try: from ofc_logic import Card, INVALID_CARD, card_to_str
    except ImportError: return WORST_RANK, "Invalid" # Не можем работать без Card

    valid_cards = [c for c in cards if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]
    num_valid = len(valid_cards)
    expected_len = len(cards)

    # Проверка на дубликаты
    if num_valid != len(set(valid_cards)):
        logger.warning(f"Duplicate cards found in hand for ranking: {[card_to_str(c) for c in valid_cards]}")
        return WORST_RANK, "Invalid"

    try:
        if expected_len == 3:
            if num_valid != 3: return WORST_RANK, "Invalid"
            rank, type_str, _ = evaluate_3_card_ofc(valid_cards[0], valid_cards[1], valid_cards[2])
            # Добавляем смещение, чтобы ранги 3-карточных рук не пересекались с 5-карточными
            # и были гарантированно хуже (больше)
            adjusted_rank = rank + WORST_RANK_5CARD
            return adjusted_rank, type_str

        elif expected_len == 5:
            if num_valid != 5: return WORST_RANK, "Invalid"
            rank = evaluator_5card.evaluate(valid_cards)
            rank_class = evaluator_5card.get_rank_class(rank)
            type_str = evaluator_5card.class_to_string(rank_class)
            return rank, type_str

        else:
            logger.warning(f"get_hand_rank_safe called with unsupported hand length {expected_len}.")
            return WORST_RANK, "Invalid"

    except Exception as e:
        hand_str = [card_to_str(c) for c in valid_cards] if valid_cards else "[]"
        logger.error(f"Error evaluating {expected_len}-card hand {hand_str}: {e}", exc_info=False)
        return WORST_RANK, "Invalid"
