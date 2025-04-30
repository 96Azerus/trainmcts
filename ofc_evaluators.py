# ofc_evaluators.py v1.8
"""
Интерфейс для оценки покерных комбинаций OFC (3 и 5 карт).
Использует специализированные модули для 3- и 5-карточных рук.
Исправлена проверка 5-карт в get_hand_rank_safe.
Добавлено детальное логгирование.
Добавлены логи перед вызовом оценщиков.
Перенесены функции check_board_foul, get_row_royalty и константы роялти из ofc_logic.py.
"""

import logging
from typing import List, Optional, Tuple, Dict # Добавлен Dict
from collections import Counter # Добавлен Counter

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

# Импортируем утилиты карт и доску из ofc_logic
try:
    from ofc_logic import Card, INVALID_CARD, card_to_str, PlayerBoard, RANK_MAP # Добавлены PlayerBoard, RANK_MAP
except ImportError:
    logging.critical("Failed to import from ofc_logic in ofc_evaluators.py")
    # Заглушки для Card, если ofc_logic недоступен
    class Card: # type: ignore
        @staticmethod
        def to_str(c): return "??"
        @staticmethod
        def get_rank_int(c): return 0 # Добавим заглушку
    class PlayerBoard: # type: ignore
        ROW_NAMES = ['top', 'middle', 'bottom']
        def __init__(self): self.rows = {r:[] for r in self.ROW_NAMES}; self.is_foul = False
        def is_complete(self): return False
    INVALID_CARD = -1
    RANK_MAP = {} # Заглушка
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

            # --- ДОБАВЛЕНО ЛОГГИРОВАНИЕ ---
            logger.debug(f"Calling evaluate_3_card_ofc with ints: {valid_cards}")
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

            # --- ДОБАВЛЕНО ЛОГГИРОВАНИЕ ---
            logger.debug(f"Calling evaluator_5card.evaluate with ints: {valid_cards}")
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


# --- Функции Подсчета Очков (Scoring) - ПЕРЕНЕСЕНЫ ИЗ ofc_logic.py ---

# Таблицы Роялти (Американские правила)
ROYALTY_BOTTOM_POINTS: Dict[str, int] = {
    "Straight": 2, "Flush": 4, "Full House": 6, "Four of a Kind": 10,
    "Straight Flush": 15, "Royal Flush": 25
}
ROYALTY_MIDDLE_POINTS: Dict[str, int] = {
    "Three of a Kind": 2, "Straight": 4, "Flush": 8, "Full House": 12,
    "Four of a Kind": 20, "Straight Flush": 30, "Royal Flush": 50
}
ROYALTY_TOP_PAIRS: Dict[int, int] = {
    RANK_MAP['6']: 1, RANK_MAP['7']: 2, RANK_MAP['8']: 3, RANK_MAP['9']: 4,
    RANK_MAP['T']: 5, RANK_MAP['J']: 6, RANK_MAP['Q']: 7, RANK_MAP['K']: 8,
    RANK_MAP['A']: 9
}
ROYALTY_TOP_TRIPS: Dict[int, int] = {
    RANK_MAP['2']: 10, RANK_MAP['3']: 11, RANK_MAP['4']: 12, RANK_MAP['5']: 13,
    RANK_MAP['6']: 14, RANK_MAP['7']: 15, RANK_MAP['8']: 16, RANK_MAP['9']: 17,
    RANK_MAP['T']: 18, RANK_MAP['J']: 19, RANK_MAP['Q']: 20, RANK_MAP['K']: 21,
    RANK_MAP['A']: 22
}

def check_board_foul(board: PlayerBoard) -> bool:
    """
    Проверяет доску на фол (нарушение порядка силы линий).
    Использует get_hand_rank_safe для получения скорректированных рангов.
    Правило: Сила Top <= Сила Middle <= Сила Bottom.
    Сильнее = Меньший ранг.
    Фол, если rank(Top) < rank(Middle) ИЛИ rank(Middle) < rank(Bottom).
    """
    if not board.is_complete():
        return False # Неполная доска не может быть фолом

    try:
        # Получаем карты как List[Optional[int]] для передачи в get_hand_rank_safe
        top_cards_opt = board.rows['top']
        mid_cards_opt = board.rows['middle']
        bot_cards_opt = board.rows['bottom']

        # Получаем скорректированные ранги
        adj_rank_t, type_t = get_hand_rank_safe(top_cards_opt)
        adj_rank_m, type_m = get_hand_rank_safe(mid_cards_opt)
        adj_rank_b, type_b = get_hand_rank_safe(bot_cards_opt)

        # Проверяем, что все ранги были успешно вычислены (не равны WORST_RANK)
        if adj_rank_t == WORST_RANK or adj_rank_m == WORST_RANK or adj_rank_b == WORST_RANK:
            logger.warning(f"Could not determine valid ranks for foul check. T:{adj_rank_t}({type_t}), M:{adj_rank_m}({type_m}), B:{adj_rank_b}({type_b}) for board:\n{board}")
            # Если не можем определить ранги, считаем не фолом, чтобы избежать ложных срабатываний
            board.is_foul = False # Устанавливаем флаг на доске
            return False

        # Логика фола: Top сильнее Middle ИЛИ Middle сильнее Bottom
        # Сильнее = Меньший скорректированный ранг
        is_foul = (adj_rank_t < adj_rank_m) or (adj_rank_m < adj_rank_b)

        # Обновляем флаг is_foul на самой доске
        board.is_foul = is_foul
        return is_foul

    except Exception as e:
        logger.error(f"Error during check_board_foul: {e}", exc_info=True)
        # При любой другой ошибке считаем не фолом
        board.is_foul = False
        return False

def get_row_royalty(cards: List[Optional[int]], row_name: str) -> int:
    """
    Вычисляет роялти для ряда. Использует глобальные эвалюаторы.
    """
    cards_str = Card.hand_to_str(cards) # Для логгирования
    logger.debug(f"Calculating royalty for row '{row_name}', cards: {cards_str}")
    if not isinstance(cards, list): return 0

    # Используем get_hand_rank_safe для получения типа руки и проверки валидности
    rank, type_str = get_hand_rank_safe(cards)
    logger.debug(f"get_hand_rank_safe returned rank={rank}, type='{type_str}' for row '{row_name}'")

    if rank == WORST_RANK: # Если рука невалидна или неполна
        logger.debug(f"Hand is invalid or incomplete, returning 0 royalty.")
        return 0

    royalty = 0
    try:
        valid_cards = [c for c in cards if c is not None and c != INVALID_CARD and c > 0]
        if not valid_cards: return 0 # На случай, если cards был [None, None, ...]

        if row_name == "top":
            logger.debug(f"Processing top row royalty. Type: '{type_str}'")
            # Типы для 3-карт: Trips, Pair, High Card
            if type_str == HAND_TYPE_TRIPS_3: # Константа импортирована
                # Исправлено: Ищем ранг трипса, а не первой карты
                ranks = [Card.get_rank_int(c) for c in valid_cards]
                rank_counts = Counter(ranks)
                trip_rank = -1
                for r, count in rank_counts.items():
                    if count == 3: trip_rank = r; break
                royalty = ROYALTY_TOP_TRIPS.get(trip_rank, 0)
                logger.debug(f"Trips detected. Trip rank index: {trip_rank}, Royalty: {royalty}")
            elif type_str == HAND_TYPE_PAIR_3: # Константа импортирована
                 ranks = [Card.get_rank_int(c) for c in valid_cards]
                 rank_counts = Counter(ranks)
                 pair_rank = -1
                 for r, count in rank_counts.items():
                     if count == 2: pair_rank = r; break
                 royalty = ROYALTY_TOP_PAIRS.get(pair_rank, 0)
                 logger.debug(f"Pair detected. Pair rank index: {pair_rank}, Royalty: {royalty}")
            else: # High Card
                 logger.debug("High card detected. Royalty: 0")
            return royalty

        elif row_name in ["middle", "bottom"]:
            logger.debug(f"Processing {row_name} row royalty. Type: '{type_str}'")
            table = ROYALTY_MIDDLE_POINTS if row_name == "middle" else ROYALTY_BOTTOM_POINTS
            hand_name = type_str # get_hand_rank_safe возвращает строку типа

            # Проверка на Royal Flush
            is_royal = False
            if hand_name == "Straight Flush":
                 ranks_int = {Card.get_rank_int(c) for c in valid_cards}
                 if ranks_int == {RANK_MAP['A'], RANK_MAP['K'], RANK_MAP['Q'], RANK_MAP['J'], RANK_MAP['T']}:
                     is_royal = True
                     logger.debug("Royal Flush detected.")

            if is_royal:
                royalty = table.get("Royal Flush", 0)
            else:
                royalty = table.get(hand_name, 0)
            logger.debug(f"Lookup in royalty table for '{'Royal Flush' if is_royal else hand_name}': {royalty}")

            # Трипс не дает роялти на боттоме
            if row_name == "bottom" and hand_name == "Three of a Kind":
                 logger.debug("Trips on bottom, setting royalty to 0.")
                 royalty = 0
            return royalty
        else:
            logger.warning(f"Unknown row name '{row_name}' in get_row_royalty.")
            return 0
    except Exception as e:
        logger.error(f"Error calculating royalty for {row_name} (Cards: {cards_str}, Type: {type_str}): {e}", exc_info=True)
        return 0
