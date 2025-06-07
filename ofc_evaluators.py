# ofc_evaluators.py v2.3 (Ensure RANK constants are from ofc_logic)
"""
Интерфейс для оценки покерных комбинаций OFC (3 и 5 карт).
Использует специализированные модули для 3- и 5-карточных рук.
Импортирует RANK_ константы из ofc_logic для консистентности.
"""

import logging
import sys # Добавлен sys
from typing import List, Optional, Tuple, Dict
from collections import Counter

try:
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD as WORST_RANK_3CARD_RAW
    from ofc_evaluator_3card import HAND_TYPE_TRIPS_3, HAND_TYPE_PAIR_3, HAND_TYPE_HIGH_CARD_3
except ImportError:
    logging.critical("Failed to import evaluate_3_card_ofc from ofc_evaluator_3card.py")
    def evaluate_3_card_ofc(c1, c2, c3) -> Tuple[int, str, str]: return (999, "Error", "ERR") # type: ignore
    WORST_RANK_3CARD_RAW = 455 # type: ignore
    HAND_TYPE_TRIPS_3 = "Trips"; HAND_TYPE_PAIR_3 = "Pair"; HAND_TYPE_HIGH_CARD_3 = "High Card" # type: ignore

try:
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_5card import LookupTable5Card
except ImportError:
    logging.critical("Failed to import evaluator_5card_instance from ofc_evaluator_5card.py")
    class MockEvaluator5Card: # type: ignore
        MAX_HIGH_CARD = 7462; WORST_RANK_5CARD = 7463
        def evaluate(self, cards): return self.WORST_RANK_5CARD
        def get_rank_class(self, rank): return 9
        def class_to_string(self, r_class): return "Error"
        table = MockEvaluator5Card()
    evaluator_5card = MockEvaluator5Card() # type: ignore
    LookupTable5Card = MockEvaluator5Card # type: ignore

try:
    from ofc_logic import (
        Card, INVALID_CARD, card_to_str, PlayerBoard, RANK_MAP, STR_RANKS,
        RANK_2, RANK_3, RANK_4, RANK_5, RANK_6, RANK_7, RANK_8, RANK_9, # Добавлены все ранги
        RANK_TEN, RANK_JACK, RANK_QUEEN, RANK_KING, RANK_ACE
    )
except ImportError:
    logging.critical("Failed to import from ofc_logic in ofc_evaluators.py")
    class Card: # type: ignore
        @staticmethod
        def to_str(c): return "??"
        @staticmethod
        def get_rank_int(c): return 0
        @staticmethod
        def get_suit_int(c): return 0
    class PlayerBoard: ROW_NAMES = ['top','middle','bottom']; ROW_CAPACITY={'top':3,'middle':5,'bottom':5};pass # type: ignore
    INVALID_CARD = -1; RANK_MAP = {}; STR_RANKS = "" # type: ignore
    RANK_2=0;RANK_3=1;RANK_4=2;RANK_5=3;RANK_6=4;RANK_7=5;RANK_8=6;RANK_9=7;RANK_TEN=8;RANK_JACK=9;RANK_QUEEN=10;RANK_KING=11;RANK_ACE=12 # type: ignore
    def card_to_str(c): return Card.to_str(c) # type: ignore


logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout) # Используем sys.stdout
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

HAND_TYPE_TO_CLASS_3CARD = { HAND_TYPE_TRIPS_3: 6, HAND_TYPE_PAIR_3: 8, HAND_TYPE_HIGH_CARD_3: 9 }
WORST_CLASS = 9
MAX_HIGH_CARD_5: int = LookupTable5Card.MAX_HIGH_CARD
WORST_RANK_5CARD_INVALID: int = LookupTable5Card.WORST_RANK_5CARD # Ранг для невалидной 5-карточной руки
WORST_RANK: int = max(MAX_HIGH_CARD_5, WORST_RANK_3CARD_RAW) + 1 # Общий худший ранг для сравнения

def get_hand_rank_safe(cards: List[Optional[int]]) -> Tuple[int, int, str]:
    hand_str_log = "N/A"; expected_len = 0
    try:
        if not isinstance(cards, list): logger.warning(f"get_hand_rank_safe non-list: {type(cards)}"); return WORST_RANK, WORST_CLASS, "Invalid"
        expected_len = len(cards); hand_str_log = [card_to_str(c) for c in cards]
        logger.debug(f"get_hand_rank_safe for: {hand_str_log} (len={expected_len})")
        valid_cards: List[int] = []
        try: valid_cards = [c for c in cards if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]
        except TypeError: logger.warning(f"Invalid elements in input list: {hand_str_log}"); return WORST_RANK, WORST_CLASS, "Invalid"
        num_valid = len(valid_cards)
        if num_valid > 1 and num_valid != len(set(valid_cards)): logger.warning(f"Duplicate cards for ranking: {hand_str_log}"); return WORST_RANK, WORST_CLASS, "Invalid"

        if expected_len == 3:
            if num_valid != 3: logger.debug(f"Invalid 3-card (expected 3, got {num_valid}): {hand_str_log}"); return WORST_RANK, WORST_CLASS, "Invalid"
            rank, type_str, _ = evaluate_3_card_ofc(valid_cards[0], valid_cards[1], valid_cards[2])
            if not (1 <= rank <= WORST_RANK_3CARD_RAW): logger.error(f"evaluate_3_card_ofc invalid raw rank: {rank} for {hand_str_log}"); return WORST_RANK, WORST_CLASS, "Invalid"
            hand_class = HAND_TYPE_TO_CLASS_3CARD.get(type_str, WORST_CLASS)
            logger.debug(f"Eval 3-card: {hand_str_log} -> RawRank:{rank}, Class:{hand_class}, Type:{type_str}")
            return rank, hand_class, type_str
        elif expected_len == 5:
            if num_valid != 5: logger.debug(f"Invalid 5-card (expected 5, got {num_valid}): {hand_str_log}"); return WORST_RANK, WORST_CLASS, "Invalid"
            rank = evaluator_5card.evaluate(valid_cards)
            if not (1 <= rank <= MAX_HIGH_CARD_5): logger.warning(f"5-card eval invalid rank: {rank} for {hand_str_log}"); return WORST_RANK, WORST_CLASS, "Invalid"
            hand_class = evaluator_5card.get_rank_class(rank); type_str = evaluator_5card.class_to_string(hand_class)
            logger.debug(f"Eval 5-card: {hand_str_log} -> Rank:{rank}, Class:{hand_class}, Type:{type_str}")
            return rank, hand_class, type_str
        else:
            # ИСПРАВЛЕНИЕ: Уровень логирования изменен с WARNING на DEBUG.
            logger.debug(f"get_hand_rank_safe unsupported length {expected_len}.")
            return WORST_RANK, WORST_CLASS, "Invalid"
    except ValueError as ve: logger.warning(f"ValueError evaluating {expected_len}-card hand {hand_str_log}: {ve}"); return WORST_RANK, WORST_CLASS, "Invalid"
    except Exception as e: logger.error(f"Unexpected error evaluating {expected_len}-card hand {hand_str_log}: {e}", exc_info=True); return WORST_RANK, WORST_CLASS, "Invalid"

ROYALTY_BOTTOM_POINTS: Dict[str, int] = { "Straight": 2, "Flush": 4, "Full House": 6, "Four of a Kind": 10, "Straight Flush": 15, "Royal Flush": 25 }
ROYALTY_MIDDLE_POINTS: Dict[str, int] = { "Three of a Kind": 2, "Straight": 4, "Flush": 8, "Full House": 12, "Four of a Kind": 20, "Straight Flush": 30, "Royal Flush": 50 }
ROYALTY_TOP_PAIRS: Dict[int, int] = { RANK_6: 1, RANK_7: 2, RANK_8: 3, RANK_9: 4, RANK_TEN: 5, RANK_JACK: 6, RANK_QUEEN: 10, RANK_KING: 11, RANK_ACE: 12 } # Используем импортированные константы
ROYALTY_TOP_TRIPS: Dict[int, int] = { RANK_2: 10, RANK_3: 11, RANK_4: 12, RANK_5: 13, RANK_6: 14, RANK_7: 15, RANK_8: 16, RANK_9: 17, RANK_TEN: 18, RANK_JACK: 19, RANK_QUEEN: 20, RANK_KING: 21, RANK_ACE: 22 }

def check_board_foul(board: PlayerBoard) -> bool:
    if not board.is_complete(): return False # Фол определяется только для полной доски
    try:
        top_r, top_c, _ = get_hand_rank_safe(board.rows['top'])
        mid_r, mid_c, _ = get_hand_rank_safe(board.rows['middle'])
        bot_r, bot_c, _ = get_hand_rank_safe(board.rows['bottom'])
        if top_r==WORST_RANK or mid_r==WORST_RANK or bot_r==WORST_RANK: logger.warning(f"Invalid ranks for foul check. T:{top_r}, M:{mid_r}, B:{bot_r}"); board.is_foul=False; return False # Ошибка оценки

        # Меньший ранг/класс означает более сильную руку
        top_vs_mid_foul = (top_c < mid_c) or (top_c == mid_c and top_r < mid_r)
        mid_vs_bot_foul = (mid_c < bot_c) or (mid_c == bot_c and mid_r < bot_r)
        is_foul = top_vs_mid_foul or mid_vs_bot_foul
        logger.debug(f"Foul check: T({top_r}/{top_c}), M({mid_r}/{mid_c}), B({bot_r}/{bot_c}) -> Foul={is_foul}")
        board.is_foul = is_foul; return is_foul
    except Exception as e: logger.error(f"Error in check_board_foul: {e}", exc_info=True); board.is_foul=False; return False

def get_row_royalty(cards: List[Optional[int]], row_name: str) -> int:
    # ... (код функции get_row_royalty без изменений, он уже использует RANK_ константы через ROYALTY_TOP_PAIRS/TRIPS) ...
    cards_str = Card.hand_to_str(cards)
    logger.debug(f"Royalty for row '{row_name}', cards: {cards_str}")
    if not isinstance(cards, list): return 0
    rank_val, hand_class_val, type_str_val = get_hand_rank_safe(cards)
    logger.debug(f"get_hand_rank_safe: rank={rank_val}, class={hand_class_val}, type='{type_str_val}' for '{row_name}'")
    if rank_val == WORST_RANK: logger.debug("Invalid/incomplete hand, 0 royalty."); return 0
    royalty_points = 0
    try:
        valid_cards_list = [c for c in cards if c is not None and c != INVALID_CARD and c > 0]
        if not valid_cards_list: return 0
        if row_name == "top":
            if len(valid_cards_list) != 3: return 0
            if hand_class_val == 6: # Trips (HAND_TYPE_TO_CLASS_3CARD[HAND_TYPE_TRIPS_3])
                ranks_list = [Card.get_rank_int(c) for c in valid_cards_list]
                trip_rank_val = next((r for r, count in Counter(ranks_list).items() if count == 3), -1)
                royalty_points = ROYALTY_TOP_TRIPS.get(trip_rank_val, 0)
            elif hand_class_val == 8: # Pair (HAND_TYPE_TO_CLASS_3CARD[HAND_TYPE_PAIR_3])
                 ranks_list = [Card.get_rank_int(c) for c in valid_cards_list]
                 pair_rank_val = next((r for r, count in Counter(ranks_list).items() if count == 2), -1)
                 royalty_points = ROYALTY_TOP_PAIRS.get(pair_rank_val, 0)
            return royalty_points
        elif row_name in ["middle", "bottom"]:
            if len(valid_cards_list) != 5: return 0
            table_to_use = ROYALTY_MIDDLE_POINTS if row_name == "middle" else ROYALTY_BOTTOM_POINTS
            is_royal_flush = (hand_class_val == 1 and rank_val == 1) # Класс 1 = SF, ранг 1 = Роял Флеш
            hand_name_for_lookup = "Royal Flush" if is_royal_flush else type_str_val
            royalty_points = table_to_use.get(hand_name_for_lookup, 0)
            if row_name == "bottom" and type_str_val == "Three of a Kind": royalty_points = 0 # Трипс на боттоме не дает роялти
            return royalty_points
        else: logger.warning(f"Unknown row '{row_name}' in get_row_royalty."); return 0
    except Exception as e: logger.error(f"Error calculating royalty for {row_name} (Cards:{cards_str}, Type:{type_str_val}): {e}", exc_info=True); return 0

def calculate_total_royalty_for_board(board: PlayerBoard) -> int:
    if not isinstance(board, PlayerBoard): logger.warning("calculate_total_royalty non-PlayerBoard."); return 0
    if check_board_foul(board): logger.debug(f"Board fouled. Total royalty 0.\n{board}"); return 0
    total_royalty_val = 0
    try:
        total_royalty_val += get_row_royalty(board.rows.get('top', []), 'top')
        total_royalty_val += get_row_royalty(board.rows.get('middle', []), 'middle')
        total_royalty_val += get_row_royalty(board.rows.get('bottom', []), 'bottom')
        logger.debug(f"Total royalty: {total_royalty_val} for board:\n{board}")
    except Exception as e: logger.error(f"Error calculating total royalty: {e}", exc_info=True); return 0
    return total_royalty_val
