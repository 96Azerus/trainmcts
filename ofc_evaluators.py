# ofc_evaluators.py v1.3
"""
Интерфейс для оценки покерных комбинаций OFC (3 и 5 карт).
Использует специализированные модули для 3- и 5-карточных рук.
Исправлены проверки рангов в get_hand_rank_safe.
"""

import logging
from typing import List, Optional, Tuple

# Импортируем конкретные эвалюаторы
try:
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD as WORST_RANK_3CARD_RAW # Переименовываем для ясности
    from ofc_evaluator_3card import HAND_TYPE_TRIPS_3, HAND_TYPE_PAIR_3, HAND_TYPE_HIGH_CARD_3 # Импортируем типы
except ImportError:
    logging.critical("Failed to import evaluate_3_card_ofc from ofc_evaluator_3card.py")
    def evaluate_3_card_ofc(c1, c2, c3) -> Tuple[int, str, str]: return (999, "Error", "ERR")
    WORST_RANK_3CARD_RAW = 455 # Заглушка
    HAND_TYPE_TRIPS_3 = "Trips"
    HAND_TYPE_PAIR_3 = "Pair"
    HAND_TYPE_HIGH_CARD_3 = "High Card"

try:
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_5card import LookupTable5Card
except ImportError:
    logging.critical("Failed to import evaluator_5card_instance from ofc_evaluator_5card.py")
    class MockEvaluator5Card:
        MAX_HIGH_CARD = 7462 # Добавляем для WORST_RANK_5CARD
        WORST_RANK_5CARD = MAX_HIGH_CARD + 1
        def evaluate(self, cards): return self.WORST_RANK_5CARD
        def get_rank_class(self, rank): return 9
        def class_to_string(self, r_class): return "Error"
    evaluator_5card = MockEvaluator5Card()
    LookupTable5Card = MockEvaluator5Card # type: ignore

# Импортируем утилиты карт для логирования и проверок
try:
    from ofc_logic import Card, INVALID_CARD, card_to_str
except ImportError:
    logging.critical("Failed to import from ofc_logic in ofc_evaluators.py")
    # Заглушки для Card, если ofc_logic недоступен
    class Card: # type: ignore
        @staticmethod
        def to_str(c): return "??"
    INVALID_CARD = -1
    def card_to_str(c): return Card.to_str(c)

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

# Ранги 5-карточных рук: 1 (лучший) - 7462 (худший)
WORST_RANK_5CARD: int = LookupTable5Card.MAX_HIGH_CARD # 7462 - это худший *валидный* ранг

# Ранги 3-карточных рук: 1 (лучший) - 455 (худший)
# WORST_RANK_3CARD_RAW уже импортирован (455)

# Скорректированный диапазон для 3-карт: добавляем смещение, чтобы они были хуже 5-карт
# Смещение = WORST_RANK_5CARD + 1 = 7463
ADJUSTMENT_3CARD: int = WORST_RANK_5CARD + 1
# Скорректированный ранг = raw_rank_3card + ADJUSTMENT_3CARD
# Лучший скорректированный = 1 + 7463 = 7464
# Худший скорректированный = 455 + 7463 = 7918
BEST_ADJUSTED_RANK_3CARD: int = 1 + ADJUSTMENT_3CARD
WORST_ADJUSTED_RANK_3CARD: int = WORST_RANK_3CARD_RAW + ADJUSTMENT_3CARD

# Общий худший ранг (гарантированно больше любого возможного *валидного* скорректированного ранга)
# Используется для обозначения ошибок/невалидных рук
WORST_RANK: int = WORST_ADJUSTED_RANK_3CARD + 1 # 7918 + 1 = 7919

# --- Основная функция оценки ---

def get_hand_rank_safe(cards: List[Optional[int]]) -> Tuple[int, str]:
    """
    Безопасно вычисляет ранг и тип руки (3 или 5 карт).
    Меньший ранг означает более сильную руку.
    3-карточные ранги смещены (добавлением ADJUSTMENT_3CARD), чтобы быть
    гарантированно хуже (численно больше) любого 5-карточного ранга.
    Возвращает WORST_RANK (7919) при любой ошибке или невалидном входе.

    Returns:
        Tuple[int, str]: Скорректированный ранг (1-7462 для 5-карт, 7464-7918 для 3-карт)
                         или WORST_RANK (7919) при ошибке, и строка типа руки ("Invalid" при ошибке).
    """
    if not isinstance(cards, list):
        logger.warning(f"get_hand_rank_safe received non-list input: {type(cards)}")
        return WORST_RANK, "Invalid"

    valid_cards: List[int] = []
    try:
        valid_cards = [c for c in cards if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]
    except TypeError: # На случай, если в cards не только int/None
        logger.warning(f"Invalid element types found in input list for ranking: {cards}")
        return WORST_RANK, "Invalid"

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

            # Получаем "сырой" ранг (1-455)
            rank, type_str, _ = evaluate_3_card_ofc(valid_cards[0], valid_cards[1], valid_cards[2])

            # FIX 1: Проверяем валидность "сырого" ранга перед корректировкой
            if not (1 <= rank <= WORST_RANK_3CARD_RAW):
                 logger.error(f"evaluate_3_card_ofc returned invalid raw rank: {rank} for hand {[card_to_str(c) for c in valid_cards]}")
                 return WORST_RANK, "Invalid" # Возвращаем ошибку, если сырой ранг некорректен

            # Корректируем ранг
            adjusted_rank = rank + ADJUSTMENT_3CARD
            logger.debug(f"Evaluated 3-card: {[card_to_str(c) for c in valid_cards]} -> RawRank: {rank}, AdjRank: {adjusted_rank}, Type: {type_str}")

            # Проверка на выход за пределы ожидаемого *скорректированного* диапазона (7464-7918)
            # Эта проверка теперь избыточна, если мы доверяем проверке сырого ранга выше, но оставим для надежности
            if not (BEST_ADJUSTED_RANK_3CARD <= adjusted_rank <= WORST_ADJUSTED_RANK_3CARD):
                 logger.error(f"Adjusted 3-card rank {adjusted_rank} out of expected range ({BEST_ADJUSTED_RANK_3CARD} - {WORST_ADJUSTED_RANK_3CARD}) for hand {[card_to_str(c) for c in valid_cards]}")
                 return WORST_RANK, "Invalid" # Возвращаем ошибку, если скорректированный ранг некорректен

            return adjusted_rank, type_str

        elif expected_len == 5:
            if num_valid != 5:
                logger.debug(f"Invalid 5-card hand (expected 5, got {num_valid}): {[card_to_str(c) for c in cards]}")
                return WORST_RANK, "Invalid"

            # Получаем ранг (1-7462)
            rank = evaluator_5card.evaluate(valid_cards)
            logger.debug(f"Evaluated 5-card: {[card_to_str(c) for c in valid_cards]} -> Rank: {rank}")

            # FIX 2: Проверяем, что ранг находится в валидном диапазоне 5-карточных рук (1 - MAX_HIGH_CARD)
            if not (1 <= rank <= WORST_RANK_5CARD): # WORST_RANK_5CARD = MAX_HIGH_CARD = 7462
                 logger.warning(f"5-card evaluation returned invalid rank: {rank} (expected 1-{WORST_RANK_5CARD}) for hand {[card_to_str(c) for c in valid_cards]}")
                 return WORST_RANK, "Invalid"

            rank_class = evaluator_5card.get_rank_class(rank)
            type_str = evaluator_5card.class_to_string(rank_class)
            return rank, type_str

        else:
            logger.warning(f"get_hand_rank_safe called with unsupported hand length {expected_len}.")
            return WORST_RANK, "Invalid"

    except ValueError as ve: # Ловим ошибки от эвалюаторов (дубликаты, невалидные карты)
        hand_str_log = [card_to_str(c) for c in cards] if cards else "[]"
        logger.warning(f"ValueError evaluating {expected_len}-card hand {hand_str_log}: {ve}")
        return WORST_RANK, "Invalid"
    except Exception as e:
        hand_str_log = [card_to_str(c) for c in cards] if cards else "[]"
        logger.error(f"Unexpected error evaluating {expected_len}-card hand {hand_str_log}: {e}", exc_info=True) # Логируем traceback для неожиданных ошибок
        return WORST_RANK, "Invalid"
