# ofc_evaluators.py v1.2
"""
Интерфейс для оценки покерных комбинаций OFC (3 и 5 карт).
Использует специализированные модули для 3- и 5-карточных рук.
Добавлено логирование в get_hand_rank_safe.
"""

import logging
from typing import List, Optional, Tuple

# Импортируем конкретные эвалюаторы
try:
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD
except ImportError:
    logging.critical("Failed to import evaluate_3_card_ofc from ofc_evaluator_3card.py")
    def evaluate_3_card_ofc(c1, c2, c3) -> Tuple[int, str, str]: return (999, "Error", "ERR")
    WORST_RANK_3CARD = 455 # Заглушка

try:
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_5card import LookupTable5Card
except ImportError:
    logging.critical("Failed to import evaluator_5card_instance from ofc_evaluator_5card.py")
    class MockEvaluator5Card:
        WORST_RANK_5CARD = 9999
        MAX_HIGH_CARD = 9999 # Добавляем для WORST_RANK
        def evaluate(self, cards): return self.WORST_RANK_5CARD
        def get_rank_class(self, rank): return 9
        def class_to_string(self, r_class): return "Error"
    evaluator_5card = MockEvaluator5Card()
    LookupTable5Card = MockEvaluator5Card

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)

# --- Константы Рангов (для удобства доступа) ---
RANK_CLASS_STRAIGHT_FLUSH: int = LookupTable5Card.MAX_STRAIGHT_FLUSH
RANK_CLASS_QUADS: int = LookupTable5Card.MAX_FOUR_OF_A_KIND
RANK_CLASS_FULL_HOUSE: int = LookupTable5Card.MAX_FULL_HOUSE
RANK_CLASS_FLUSH: int = LookupTable5Card.MAX_FLUSH
RANK_CLASS_STRAIGHT: int = LookupTable5Card.MAX_STRAIGHT
RANK_CLASS_TRIPS_5: int = LookupTable5Card.MAX_THREE_OF_A_KIND
RANK_CLASS_TWO_PAIR: int = LookupTable5Card.MAX_TWO_PAIR
RANK_CLASS_PAIR_5: int = LookupTable5Card.MAX_PAIR
RANK_CLASS_HIGH_CARD_5: int = LookupTable5Card.MAX_HIGH_CARD
WORST_RANK_5CARD: int = LookupTable5Card.WORST_RANK_5CARD

HAND_TYPE_TRIPS_3: str = "Trips"
HAND_TYPE_PAIR_3: str = "Pair"
HAND_TYPE_HIGH_CARD_3: str = "High Card"
WORST_RANK_3CARD_RAW: int = WORST_RANK_3CARD # Используем импортированное значение
WORST_RANK_3CARD_ADJUSTED: int = WORST_RANK_5CARD + WORST_RANK_3CARD_RAW # Скорректированный

# Общий худший ранг (гарантированно больше любого возможного ранга)
WORST_RANK: int = WORST_RANK_3CARD_ADJUSTED + 1

# --- Основная функция оценки ---

def get_hand_rank_safe(cards: List[Optional[int]]) -> Tuple[int, str]:
    """
    Безопасно вычисляет ранг и тип руки (3 или 5 карт).
    Меньший ранг означает более сильную руку.
    3-карточные ранги смещены, чтобы быть гарантированно хуже 5-карточных.
    """
    if not isinstance(cards, list):
        logger.warning(f"get_hand_rank_safe received non-list input: {type(cards)}")
        return WORST_RANK, "Invalid"

    try: from ofc_logic import Card, INVALID_CARD, card_to_str
    except ImportError: return WORST_RANK, "Invalid"

    valid_cards = [c for c in cards if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]
    num_valid = len(valid_cards)
    expected_len = len(cards) # Ожидаемая длина исходного списка

    # Проверяем дубликаты только если есть что проверять
    if num_valid > 1 and num_valid != len(set(valid_cards)):
        logger.warning(f"Duplicate cards found in hand for ranking: {[card_to_str(c) for c in valid_cards]}")
        return WORST_RANK, "Invalid"

    try:
        if expected_len == 3:
            if num_valid != 3:
                logger.debug(f"Invalid 3-card hand (expected 3, got {num_valid}): {[card_to_str(c) for c in cards]}")
                return WORST_RANK, "Invalid"
            rank, type_str, _ = evaluate_3_card_ofc(valid_cards[0], valid_cards[1], valid_cards[2])
            # Добавляем смещение WORST_RANK_5CARD
            adjusted_rank = rank + WORST_RANK_5CARD
            logger.debug(f"Evaluated 3-card: {[card_to_str(c) for c in valid_cards]} -> Rank: {rank}, AdjRank: {adjusted_rank}, Type: {type_str}")
            # Проверка на выход за пределы ожидаемого диапазона
            if not (WORST_RANK_5CARD < adjusted_rank <= WORST_RANK_3CARD_ADJUSTED):
                 logger.error(f"Adjusted 3-card rank {adjusted_rank} out of expected range ({WORST_RANK_5CARD+1} - {WORST_RANK_3CARD_ADJUSTED}) for hand {[card_to_str(c) for c in valid_cards]}")
                 return WORST_RANK, "Invalid" # Возвращаем ошибку, если ранг некорректен
            return adjusted_rank, type_str

        elif expected_len == 5:
            if num_valid != 5:
                logger.debug(f"Invalid 5-card hand (expected 5, got {num_valid}): {[card_to_str(c) for c in cards]}")
                return WORST_RANK, "Invalid"
            rank = evaluator_5card.evaluate(valid_cards)
            logger.debug(f"Evaluated 5-card: {[card_to_str(c) for c in valid_cards]} -> Rank: {rank}")
            # Проверяем, что ранг валиден перед получением класса
            if not (1 <= rank <= WORST_RANK_5CARD): # Ранг должен быть в допустимом диапазоне 5-карт
                 logger.warning(f"5-card evaluation returned invalid rank: {rank} for hand {[card_to_str(c) for c in valid_cards]}")
                 return WORST_RANK, "Invalid"
            rank_class = evaluator_5card.get_rank_class(rank)
            type_str = evaluator_5card.class_to_string(rank_class)
            return rank, type_str

        else:
            logger.warning(f"get_hand_rank_safe called with unsupported hand length {expected_len}.")
            return WORST_RANK, "Invalid"

    except ValueError as ve: # Ловим ошибки от эвалюаторов (дубликаты, невалидные карты)
        hand_str = [card_to_str(c) for c in cards] if cards else "[]"
        logger.warning(f"ValueError evaluating {expected_len}-card hand {hand_str}: {ve}")
        return WORST_RANK, "Invalid"
    except Exception as e:
        hand_str = [card_to_str(c) for c in cards] if cards else "[]"
        logger.error(f"Unexpected error evaluating {expected_len}-card hand {hand_str}: {e}", exc_info=True) # Логируем traceback для неожиданных ошибок
        return WORST_RANK, "Invalid"
