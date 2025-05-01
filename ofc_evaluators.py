# ofc_evaluators.py v1.9
"""
Интерфейс для оценки покерных комбинаций OFC (3 и 5 карт).
Использует специализированные модули для 3- и 5-карточных рук.
Исправлена проверка 5-карт в get_hand_rank_safe.
Добавлено детальное логгирование.
Перенесены функции check_board_foul, get_row_royalty и константы роялти из ofc_logic.py.
ИСПРАВЛЕНО: get_hand_rank_safe теперь возвращает СЫРОЙ ранг и КЛАСС руки.
ИСПРАВЛЕНО: check_board_foul теперь сравнивает КЛАССЫ и СЫРЫЕ ранги.
ИСПРАВЛЕНО: get_row_royalty использует класс руки.
"""

import logging
from typing import List, Optional, Tuple, Dict # Добавлен Dict
from collections import Counter # Добавлен Counter

# Импортируем конкретные эвалюаторы
try:
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD as WORST_RANK_3CARD_RAW
    from ofc_evaluator_3card import HAND_TYPE_TRIPS_3, HAND_TYPE_PAIR_3, HAND_TYPE_HIGH_CARD_3
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
    from ofc_logic import Card, INVALID_CARD, card_to_str, PlayerBoard, RANK_MAP
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
    logger.setLevel(logging.WARNING) # Возвращаем уровень WARNING по умолчанию
    handler = logging.StreamHandler() # Вывод в консоль
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- ДОБАВЛЕНО: Маппинг 3-карточных типов в классы (аналогично 5-карточным) ---
# Меньший класс = сильнее
HAND_TYPE_TO_CLASS_3CARD = {
    HAND_TYPE_TRIPS_3: 6, # Сопоставляем с классом 5-карточного Трипса
    HAND_TYPE_PAIR_3: 8,  # Сопоставляем с классом 5-карточной Пары
    HAND_TYPE_HIGH_CARD_3: 9 # Сопоставляем с классом 5-карточной Старшей карты
}
# Общий худший класс
WORST_CLASS = 9

# --- Константы Рангов (для удобства доступа) ---
# Используем значения из LookupTable5Card для консистентности
MAX_HIGH_CARD_5: int = LookupTable5Card.MAX_HIGH_CARD # 7462 - худший *валидный* 5-карточный ранг
WORST_RANK_5CARD_INVALID: int = LookupTable5Card.WORST_RANK_5CARD # 7463 - *невалидный* 5-карточный ранг (для ошибок)

# Ранги 3-карточных рук: 1 (лучший) - 455 (худший)
# WORST_RANK_3CARD_RAW уже импортирован (455)

# Общий худший ранг (для ошибок)
WORST_RANK: int = max(MAX_HIGH_CARD_5, WORST_RANK_3CARD_RAW) + 1 # 7462 + 1 = 7463

# --- Основная функция оценки ---

def get_hand_rank_safe(cards: List[Optional[int]]) -> Tuple[int, int, str]:
    """
    Безопасно вычисляет СЫРОЙ ранг, КЛАСС руки и тип руки (3 или 5 карт).
    Меньший ранг/класс означает более сильную руку.
    Возвращает (WORST_RANK, WORST_CLASS, "Invalid") при любой ошибке.

    Returns:
        Tuple[int, int, str]: (Сырой ранг, Класс руки, Строка типа руки)
                              Класс: 1=SF, 2=4oak, 3=FH, 4=Flush, 5=Straight,
                                     6=Trips/3oak, 7=2Pair, 8=Pair, 9=HighCard
    """
    hand_str_log = "N/A"
    expected_len = 0
    try:
        if not isinstance(cards, list):
            logger.warning(f"get_hand_rank_safe received non-list input: {type(cards)}")
            return WORST_RANK, WORST_CLASS, "Invalid"

        expected_len = len(cards)
        hand_str_log = [card_to_str(c) for c in cards]
        logger.debug(f"get_hand_rank_safe called for: {hand_str_log} (expected_len={expected_len})")

        valid_cards: List[int] = []
        try:
            valid_cards = [c for c in cards if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]
        except TypeError:
            logger.warning(f"Invalid element types found in input list for ranking: {hand_str_log}")
            return WORST_RANK, WORST_CLASS, "Invalid"

        num_valid = len(valid_cards)

        if num_valid > 1 and num_valid != len(set(valid_cards)):
            logger.warning(f"Duplicate cards found in hand for ranking: {hand_str_log}")
            return WORST_RANK, WORST_CLASS, "Invalid"


        if expected_len == 3:
            if num_valid != 3:
                logger.debug(f"Invalid 3-card hand (expected 3, got {num_valid}): {hand_str_log}")
                return WORST_RANK, WORST_CLASS, "Invalid"

            logger.debug(f"Calling evaluate_3_card_ofc with ints: {valid_cards}")
            rank, type_str, rank_str = evaluate_3_card_ofc(valid_cards[0], valid_cards[1], valid_cards[2])
            logger.debug(f"evaluate_3_card_ofc returned: rank={rank}, type={type_str}, rank_str={rank_str} for {hand_str_log}")

            if not (1 <= rank <= WORST_RANK_3CARD_RAW):
                 logger.error(f"evaluate_3_card_ofc returned invalid raw rank: {rank} for hand {hand_str_log}")
                 return WORST_RANK, WORST_CLASS, "Invalid"

            # Получаем класс руки
            hand_class = HAND_TYPE_TO_CLASS_3CARD.get(type_str, WORST_CLASS)
            logger.debug(f"Evaluated 3-card: {hand_str_log} -> RawRank: {rank}, Class: {hand_class}, Type: {type_str}")
            return rank, hand_class, type_str

        elif expected_len == 5:
            if num_valid != 5:
                logger.debug(f"Invalid 5-card hand (expected 5, got {num_valid}): {hand_str_log}")
                return WORST_RANK, WORST_CLASS, "Invalid"

            logger.debug(f"Calling evaluator_5card.evaluate with ints: {valid_cards}")
            rank = evaluator_5card.evaluate(valid_cards)
            logger.debug(f"evaluator_5card.evaluate returned: {rank} for {hand_str_log}")

            if not (1 <= rank <= MAX_HIGH_CARD_5):
                 logger.warning(f"5-card evaluation returned invalid rank: {rank} (expected 1-{MAX_HIGH_CARD_5}) for hand {hand_str_log}")
                 return WORST_RANK, WORST_CLASS, "Invalid"

            # Получаем класс руки
            hand_class = evaluator_5card.get_rank_class(rank)
            type_str = evaluator_5card.class_to_string(hand_class)
            logger.debug(f"Evaluated 5-card: {hand_str_log} -> Rank: {rank}, Class: {hand_class}, Type: {type_str}")
            return rank, hand_class, type_str

        else:
            logger.warning(f"get_hand_rank_safe called with unsupported hand length {expected_len}.")
            return WORST_RANK, WORST_CLASS, "Invalid"

    except ValueError as ve:
        logger.warning(f"ValueError evaluating {expected_len}-card hand {hand_str_log}: {ve}")
        return WORST_RANK, WORST_CLASS, "Invalid"
    except Exception as e:
        logger.error(f"Unexpected error evaluating {expected_len}-card hand {hand_str_log}: {e}", exc_info=True)
        return WORST_RANK, WORST_CLASS, "Invalid"


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
    Использует get_hand_rank_safe для получения СЫРЫХ рангов и КЛАССОВ рук.
    Сравнивает сначала классы, потом ранги.
    Фол, если Top сильнее Middle ИЛИ Middle сильнее Bottom.
    """
    if not board.is_complete():
        return False

    try:
        top_cards_opt = board.rows['top']
        mid_cards_opt = board.rows['middle']
        bot_cards_opt = board.rows['bottom']

        # Получаем ранг, класс и тип
        rank_t, class_t, type_t = get_hand_rank_safe(top_cards_opt)
        rank_m, class_m, type_m = get_hand_rank_safe(mid_cards_opt)
        rank_b, class_b, type_b = get_hand_rank_safe(bot_cards_opt)

        # Проверяем, что все ранги/классы валидны
        if rank_t == WORST_RANK or rank_m == WORST_RANK or rank_b == WORST_RANK:
            logger.warning(f"Could not determine valid ranks/classes for foul check. T:{rank_t}/{class_t}({type_t}), M:{rank_m}/{class_m}({type_m}), B:{rank_b}/{class_b}({type_b}) for board:\n{board}")
            board.is_foul = False
            return False

        # Логика фола с использованием классов и рангов
        # Фол, если Top сильнее Middle
        top_stronger_mid = (class_t < class_m) or (class_t == class_m and rank_t < rank_m)
        # Фол, если Middle сильнее Bottom
        mid_stronger_bot = (class_m < class_b) or (class_m == class_b and rank_m < rank_b) # Исправлена опечатка

        is_foul = top_stronger_mid or mid_stronger_bot
        logger.debug(f"Foul check: T={rank_t}/{class_t}({type_t}), M={rank_m}/{class_m}({type_m}), B={rank_b}/{class_b}({type_b}) -> top_stronger={top_stronger_mid}, mid_stronger={mid_stronger_bot} -> is_foul={is_foul}")

        board.is_foul = is_foul
        return is_foul

    except Exception as e:
        logger.error(f"Error during check_board_foul: {e}", exc_info=True)
        board.is_foul = False
        return False

def get_row_royalty(cards: List[Optional[int]], row_name: str) -> int:
    """
    Вычисляет роялти для ряда. Использует глобальные эвалюаторы.
    """
    cards_str = Card.hand_to_str(cards)
    logger.debug(f"Calculating royalty for row '{row_name}', cards: {cards_str}")
    if not isinstance(cards, list): return 0

    # Получаем ранг, класс и тип
    rank, hand_class, type_str = get_hand_rank_safe(cards)
    logger.debug(f"get_hand_rank_safe returned rank={rank}, class={hand_class}, type='{type_str}' for row '{row_name}'")

    if rank == WORST_RANK:
        logger.debug(f"Hand is invalid or incomplete, returning 0 royalty.")
        return 0

    royalty = 0
    try:
        valid_cards = [c for c in cards if c is not None and c != INVALID_CARD and c > 0]
        if not valid_cards: return 0

        if row_name == "top":
            logger.debug(f"Processing top row royalty. Class: {hand_class}, Type: '{type_str}'")
            # Используем класс для определения типа
            if hand_class == 6: # Trips
                ranks = [Card.get_rank_int(c) for c in valid_cards]
                rank_counts = Counter(ranks)
                trip_rank = -1
                for r, count in rank_counts.items():
                    if count == 3: trip_rank = r; break
                royalty = ROYALTY_TOP_TRIPS.get(trip_rank, 0)
                logger.debug(f"Trips detected. Trip rank index: {trip_rank}, Royalty: {royalty}")
            elif hand_class == 8: # Pair
                 ranks = [Card.get_rank_int(c) for c in valid_cards]
                 rank_counts = Counter(ranks)
                 pair_rank = -1
                 for r, count in rank_counts.items():
                     if count == 2: pair_rank = r; break
                 royalty = ROYALTY_TOP_PAIRS.get(pair_rank, 0)
                 logger.debug(f"Pair detected. Pair rank index: {pair_rank}, Royalty: {royalty}")
            else: # High Card (class 9)
                 logger.debug("High card detected. Royalty: 0")
            return royalty

        elif row_name in ["middle", "bottom"]:
            logger.debug(f"Processing {row_name} row royalty. Class: {hand_class}, Type: '{type_str}'")
            table = ROYALTY_MIDDLE_POINTS if row_name == "middle" else ROYALTY_BOTTOM_POINTS
            hand_name = type_str # Используем строку типа, возвращенную get_hand_rank_safe

            # Проверка на Royal Flush (класс 1, ранг 1)
            is_royal = (hand_class == 1 and rank == 1)
            if is_royal: logger.debug("Royal Flush detected.")

            if is_royal:
                royalty = table.get("Royal Flush", 0)
            else:
                royalty = table.get(hand_name, 0)
            logger.debug(f"Lookup in royalty table for '{'Royal Flush' if is_royal else hand_name}': {royalty}")

            # Трипс (класс 6) не дает роялти на боттоме
            if row_name == "bottom" and hand_class == 6:
                 logger.debug("Trips on bottom, setting royalty to 0.")
                 royalty = 0
            return royalty
        else:
            logger.warning(f"Unknown row name '{row_name}' in get_row_royalty.")
            return 0
    except Exception as e:
        logger.error(f"Error calculating royalty for {row_name} (Cards: {cards_str}, Type: {type_str}): {e}", exc_info=True)
        return 0
