# ofc_evaluators.py v1.6
"""
Интерфейс для оценки покерных комбинаций OFC (3 и 5 карт).
Использует специализированные модули для 3- и 5-карточных рук.
Исправлена проверка 5-карт в get_hand_rank_safe.
Добавлено детальное логгирование.
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
        MAX_HIGH_CARD = 7462 # Худший валидный ранг
        WORST_RANK_5CARD = MAX_HIGH_CARD + 1 # Невалидный ранг
        def evaluate(self, cards): return self.WORST_RANK_5CARD
        def get_rank_class(self, rank): return 9
        def class_to_string(self, r_class): return "Error"
        table = MockEvaluator5Card() # type: ignore
    evaluator_5card = MockEvaluator5Card() # type: ignore
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
    logger.setLevel(logging.DEBUG) # Устанавливаем DEBUG для отладки
    handler = logging.StreamHandler() # Вывод в консоль
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- Константы Рангов (для удобства доступа) ---
# Используем значения из LookupTable5Card для консистентности
MAX_HIGH_CARD_5: int = LookupTable5Card.MAX_HIGH_CARD # 7462 - худший *валидный* 5-карточный ранг
WORST_RANK_5CARD_INVALID: int = LookupTable5Card.WORST_RANK_5CARD # 7463 - *невалидный* 5-карточный ранг (для ошибок)

# Ранги 3-карточных рук: 1 (лучший) - 455 (худший)
# WORST_RANK_3CARD_RAW уже импортирован (455)

# Скорректированный диапазон для 3-карт
ADJUSTMENT_3CARD: int = WORST_RANK_5CARD_INVALID # Смещение = 7463
BEST_ADJUSTED_RANK_3CARD: int = 1 + ADJUSTMENT_3CARD # 7464
WORST_ADJUSTED_RANK_3CARD: int = WORST_RANK_3CARD_RAW + ADJUSTMENT_3CARD # 7918

# Общий худший ранг (для ошибок)
WORST_RANK: int = WORST_ADJUSTED_RANK_3CARD + 1 # 7919

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
    hand_str_log = "N/A" # Для логгирования в случае ошибки до обработки карт
    expected_len = 0
    try:
        if not isinstance(cards, list):
            logger.warning(f"get_hand_rank_safe received non-list input: {type(cards)}")
            return WORST_RANK, "Invalid"

        expected_len = len(cards) # Ожидаемая длина исходного списка
        hand_str_log = [card_to_str(c) for c in cards] # Логируем исходный вид руки
        logger.debug(f"get_hand_rank_safe called for: {hand_str_log} (expected_len={expected_len})")

        valid_cards: List[int] = []
        try:
            valid_cards = [c for c in cards if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]
        except TypeError: # На случай, если в cards не только int/None
            logger.warning(f"Invalid element types found in input list for ranking: {hand_str_log}")
            return WORST_RANK, "Invalid"

        num_valid = len(valid_cards)

        # Проверяем дубликаты только если есть что проверять
        if num_valid > 1 and num_valid != len(set(valid_cards)):
            logger.warning(f"Duplicate cards found in hand for ranking: {hand_str_log}")
            return WORST_RANK, "Invalid"

        if expected_len == 3:
            if num_valid != 3:
                logger.debug(f"Invalid 3-card hand (expected 3, got {num_valid}): {hand_str_log}")
                return WORST_RANK, "Invalid"

            # Получаем "сырой" ранг (1-455)
            rank, type_str, rank_str = evaluate_3_card_ofc(valid_cards[0], valid_cards[1], valid_cards[2])
            logger.debug(f"evaluate_3_card_ofc returned: rank={rank}, type={type_str}, rank_str={rank_str} for {hand_str_log}")

            # Проверяем валидность "сырого" ранга перед корректировкой
            if not (1 <= rank <= WORST_RANK_3CARD_RAW):
                 logger.error(f"evaluate_3_card_ofc returned invalid raw rank: {rank} for hand {hand_str_log}")
                 return WORST_RANK, "Invalid" # Возвращаем ошибку, если сырой ранг некорректен

            # Корректируем ранг
            adjusted_rank = rank + ADJUSTMENT_3CARD
            logger.debug(f"Evaluated 3-card: {hand_str_log} -> RawRank: {rank}, AdjRank: {adjusted_rank}, Type: {type_str}")

            # Проверка на выход за пределы ожидаемого *скорректированного* диапазона (7464-7918)
            if not (BEST_ADJUSTED_RANK_3CARD <= adjusted_rank <= WORST_ADJUSTED_RANK_3CARD):
                 logger.error(f"Adjusted 3-card rank {adjusted_rank} out of expected range ({BEST_ADJUSTED_RANK_3CARD} - {WORST_ADJUSTED_RANK_3CARD}) for hand {hand_str_log}")
                 return WORST_RANK, "Invalid" # Возвращаем ошибку, если скорректированный ранг некорректен

            return adjusted_rank, type_str

        elif expected_len == 5:
            if num_valid != 5:
                logger.debug(f"Invalid 5-card hand (expected 5, got {num_valid}): {hand_str_log}")
                return WORST_RANK, "Invalid"

            # Получаем ранг (1-7462 или 7463 при ошибке)
            rank = evaluator_5card.evaluate(valid_cards)
            logger.debug(f"evaluator_5card.evaluate returned: {rank} for {hand_str_log}")

            # FIX 18: Проверяем, что ранг находится в валидном диапазоне 5-карточных рук (1 - MAX_HIGH_CARD_5)
            if not (1 <= rank <= MAX_HIGH_CARD_5): # Проверяем диапазон 1-7462
                 logger.warning(f"5-card evaluation returned invalid rank: {rank} (expected 1-{MAX_HIGH_CARD_5}) for hand {hand_str_log}")
                 return WORST_RANK, "Invalid" # Возвращаем общий WORST_RANK (7919)

            # Если ранг валиден (1-7462), получаем класс и строку
            rank_class = evaluator_5card.get_rank_class(rank)
            type_str = evaluator_5card.class_to_string(rank_class)
            logger.debug(f"Evaluated 5-card: {hand_str_log} -> Rank: {rank}, Type: {type_str}")
            return rank, type_str

        else:
            logger.warning(f"get_hand_rank_safe called with unsupported hand length {expected_len}.")
            return WORST_RANK, "Invalid"

    except ValueError as ve: # Ловим ошибки от эвалюаторов (дубликаты, невалидные карты, ошибки таблицы)
        logger.warning(f"ValueError evaluating {expected_len}-card hand {hand_str_log}: {ve}")
        return WORST_RANK, "Invalid"
    except Exception as e:
        logger.error(f"Unexpected error evaluating {expected_len}-card hand {hand_str_log}: {e}", exc_info=True) # Логируем traceback для неожиданных ошибок
        return WORST_RANK, "Invalid"
