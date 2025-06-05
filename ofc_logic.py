# ofc_logic.py v2.1 (Added UNKNOWN_CARD_MARKER_LOGIC, minor logging change)
"""
Базовая логика игры OFC Pineapple: Карты, Колода, Доска, Константы.
Добавлен UNKNOWN_CARD_MARKER_LOGIC для использования на бэкенде.
"""

import random
import copy
import logging
import sys # Добавлен sys для StreamHandler
from typing import List, Tuple, Dict, Optional, Set, Any
from collections import Counter

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.INFO) # Уровень INFO для основного логгера модуля
    handler = logging.StreamHandler(sys.stdout) # Используем sys.stdout
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- Константы Карт ---
STR_RANKS: str = '23456789TJQKA'
INT_RANKS: range = range(13) # 0 for '2', ..., 12 for 'A'
PRIMES: List[int] = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41] # Для оценки рук

RANK_CHAR_TO_INT: Dict[str, int] = {rank: i for i, rank in enumerate(STR_RANKS)}
SUIT_CHAR_TO_INT: Dict[str, int] = {'s': 1, 'h': 2, 'd': 4, 'c': 8} # Spades, Hearts, Diamonds, Clubs

INT_RANK_TO_CHAR: Dict[int, str] = {i: rank for i, rank in enumerate(STR_RANKS)}
INT_SUIT_TO_CHAR: Dict[int, str] = {1: 's', 2: 'h', 4: 'd', 8: 'c'}

RANK_MAP: Dict[str, int] = RANK_CHAR_TO_INT # Алиас
SUIT_MAP: Dict[str, int] = SUIT_CHAR_TO_INT # Алиас

# Константы для конкретных рангов (используются в эвристиках и оценке Фантазии)
RANK_2 = RANK_MAP['2']
RANK_3 = RANK_MAP['3']
RANK_4 = RANK_MAP['4']
RANK_5 = RANK_MAP['5']
RANK_6 = RANK_MAP['6']
RANK_7 = RANK_MAP['7']
RANK_8 = RANK_MAP['8']
RANK_9 = RANK_MAP['9']
RANK_TEN = RANK_MAP['T']
RANK_JACK = RANK_MAP['J']
RANK_QUEEN = RANK_MAP['Q']
RANK_KING = RANK_MAP['K']
RANK_ACE = RANK_MAP['A']


INVALID_CARD: int = -1
CARD_PLACEHOLDER: str = "__" # Для отображения пустых слотов на фронтенде
UNKNOWN_CARD_MARKER_LOGIC: str = "??" # Маркер для "?" карт, используемый в Python логике (app.py)
NUM_CARDS: int = 52

# --- Класс Card (Утилиты) ---
class Card:
    @staticmethod
    def from_str(card_str: str) -> int:
        if not isinstance(card_str, str):
             raise TypeError(f"Input must be str, got {type(card_str)}")
        if card_str == UNKNOWN_CARD_MARKER_LOGIC:
            raise ValueError(f"Cannot convert UNKNOWN_CARD_MARKER_LOGIC ('{UNKNOWN_CARD_MARKER_LOGIC}') to card int via from_str.")

        rank_char_input = card_str[:-1].upper()
        suit_char_input = card_str[-1].lower()

        if rank_char_input == "10": rank_char = 'T'
        else: rank_char = rank_char_input
        
        suit_char = suit_char_input

        rank_int = RANK_CHAR_TO_INT.get(rank_char)
        suit_int = SUIT_CHAR_TO_INT.get(suit_char)

        if rank_int is None: raise ValueError(f"Invalid rank char: '{rank_char}' from '{card_str}'")
        if suit_int is None: raise ValueError(f"Invalid suit char: '{suit_char}' from '{card_str}'")

        try: rank_prime = PRIMES[rank_int]
        except IndexError: raise ValueError(f"Internal error: Prime not found for rank_int {rank_int}")

        bitrank = 1 << (rank_int + 16) # Сдвиг на 16, чтобы не пересекаться с другими битами
        suit = suit_int << 12         # Масть в битах 12-15
        rank = rank_int << 8          # Ранг в битах 8-11
        return bitrank | suit | rank | rank_prime # Простое число в битах 0-5

    @staticmethod
    def to_str(card_int: Optional[int]) -> str:
        if card_int is None or card_int == INVALID_CARD or not isinstance(card_int, int) or card_int <= 0:
            return CARD_PLACEHOLDER
        try:
            rank_int = Card.get_rank_int(card_int)
            suit_int = Card.get_suit_int(card_int)
            rank_char = INT_RANK_TO_CHAR.get(rank_int)
            suit_char = INT_SUIT_TO_CHAR.get(suit_int)
            if rank_char and suit_char: return rank_char + suit_char
            else: logger.warning(f"Could not convert card int {card_int} (rank={rank_int}, suit={suit_int})"); return CARD_PLACEHOLDER
        except Exception as e: logger.error(f"Error converting card int {card_int} to string: {e}"); return CARD_PLACEHOLDER

    @staticmethod
    def get_rank_int(card_int: int) -> int: return (card_int >> 8) & 0xF
    @staticmethod
    def get_suit_int(card_int: int) -> int: return (card_int >> 12) & 0xF
    @staticmethod
    def get_prime(card_int: int) -> int: return card_int & 0x3F # Первые 6 бит

    @staticmethod
    def hand_to_int(card_strs: List[Optional[str]]) -> List[Optional[int]]:
        hand_ints: List[Optional[int]] = []
        if not isinstance(card_strs, list):
            logger.warning(f"hand_to_int expected list, got {type(card_strs)}")
            try: length = len(card_strs) # type: ignore
            except TypeError: length = 0
            return [None] * length
        for s_card in card_strs:
            if s_card is None or s_card == CARD_PLACEHOLDER or not isinstance(s_card, str) or s_card == UNKNOWN_CARD_MARKER_LOGIC:
                hand_ints.append(None)
            elif len(s_card) < 2 : hand_ints.append(None)
            else:
                try: hand_ints.append(Card.from_str(s_card))
                except (ValueError, TypeError): hand_ints.append(None) # Не логируем ошибку здесь, т.к. from_str уже это делает
        return hand_ints

    @staticmethod
    def hand_to_str(card_ints: List[Optional[int]]) -> List[str]:
        if not isinstance(card_ints, list):
            logger.warning(f"hand_to_str expected list, got {type(card_ints)}")
            try: length = len(card_ints) # type: ignore
            except TypeError: length = 0
            return [CARD_PLACEHOLDER] * length
        return [Card.to_str(c) for c in card_ints]

def card_to_str(card_int: Optional[int]) -> str: return Card.to_str(card_int)

class Deck:
    _FULL_DECK_STRS: Set[str] = {r + s for r in STR_RANKS for s_char_key in SUIT_CHAR_TO_INT.keys() for s in [s_char_key]}
    FULL_DECK_CARDS: Set[int] = set()
    _init_error_deck: bool = False
    try:
        for card_s_init in _FULL_DECK_STRS:
            try:
                card_i = Card.from_str(card_s_init)
                if not (isinstance(card_i, int) and card_i != INVALID_CARD and card_i > 0):
                    logger.error(f"Invalid card int {card_i} for '{card_s_init}' in Deck init."); _init_error_deck = True
                else: FULL_DECK_CARDS.add(card_i)
            except (ValueError, TypeError) as e_c: logger.error(f"Error converting '{card_s_init}' in Deck init: {e_c}"); _init_error_deck = True
        if len(FULL_DECK_CARDS) != 52 or _init_error_deck:
            logger.critical(f"Deck init failed! Expected 52, got {len(FULL_DECK_CARDS)}. Error: {_init_error_deck}")
            raise RuntimeError("Failed to initialize standard 52-card deck.")
    except Exception as e_deck_init: logger.critical(f"CRITICAL Deck init error: {e_deck_init}", exc_info=True); raise RuntimeError("Deck init failed.") from e_deck_init

    def __init__(self, cards: Optional[Set[int]] = None):
        if cards is None: self.cards: Set[int] = self.FULL_DECK_CARDS.copy()
        else:
            self.cards: Set[int] = {c for c in cards if isinstance(c, int) and c != INVALID_CARD and c > 0}
            if len(self.cards) != len(cards): logger.warning(f"Deck init with {len(self.cards)}, filtered from {len(cards)}.")

    def deal(self, n: int) -> List[int]:
        if not (isinstance(n, int) and n > 0): return []
        num_avail = len(self.cards); num_to_deal = min(n, num_avail)
        if n > num_avail: logger.debug(f"Deck.deal: To deal {n}, only {num_avail} left.")
        if num_to_deal == 0: return []
        try:
            dealt = random.sample(list(self.cards), num_to_deal) # random.sample требует последовательность
            self.cards.difference_update(dealt)
            return dealt
        except Exception as e: logger.error(f"Error in Deck.deal({n}): {e}", exc_info=True); return []

    def remove(self, cards_to_remove: List[int]):
        if not isinstance(cards_to_remove, list): logger.warning(f"Deck.remove expected list, got {type(cards_to_remove)}"); return
        valid_to_remove = {c for c in cards_to_remove if isinstance(c, int) and c != INVALID_CARD and c > 0}
        self.cards.difference_update(valid_to_remove)

    def get_remaining_cards(self) -> List[int]: return list(self.cards)
    def copy(self) -> 'Deck': return Deck(self.cards.copy())
    def __len__(self) -> int: return len(self.cards)
    def __contains__(self, card: int) -> bool: return isinstance(card, int) and card != INVALID_CARD and card > 0 and card in self.cards
    def __str__(self) -> str: return f"Deck({len(self.cards)} cards)"
    def __repr__(self) -> str: return self.__str__()

class PlayerBoard:
    ROW_CAPACITY: Dict[str, int] = {'top': 3, 'middle': 5, 'bottom': 5}
    ROW_NAMES: List[str] = ['top', 'middle', 'bottom']
    TOTAL_CAPACITY: int = sum(ROW_CAPACITY.values())

    def __init__(self):
        self.rows: Dict[str, List[Optional[int]]] = {n: [None] * c for n, c in self.ROW_CAPACITY.items()}
        self._cards_placed: int = 0
        self.is_foul: bool = False # Устанавливается внешней логикой (ofc_evaluators.check_board_foul)

    def add_card(self, card_int: int, row_name: str, index: int) -> bool:
        if row_name not in self.ROW_NAMES: logger.warning(f"Invalid row '{row_name}'."); return False
        if not (isinstance(card_int, int) and card_int != INVALID_CARD and card_int > 0): logger.warning(f"Invalid card_int '{card_int}'."); return False
        capacity = self.ROW_CAPACITY[row_name]
        if not (0 <= index < capacity): logger.warning(f"Invalid index {index} for row '{row_name}'."); return False
        if self.rows[row_name][index] is not None: logger.debug(f"Slot {row_name}[{index}] occupied by {Card.to_str(self.rows[row_name][index])}."); return False
        if card_int in self.get_all_cards(): logger.warning(f"Duplicate card {Card.to_str(card_int)} on board."); return False
        self.rows[row_name][index] = card_int; self._cards_placed += 1; self.is_foul = False # Сброс флага фола при изменении
        return True

    def set_full_board(self, top: List[int], middle: List[int], bottom: List[int]):
        if not (isinstance(top,list) and isinstance(middle,list) and isinstance(bottom,list)): raise TypeError("Inputs must be lists.")
        if len(top)!=self.ROW_CAPACITY['top'] or len(middle)!=self.ROW_CAPACITY['middle'] or len(bottom)!=self.ROW_CAPACITY['bottom']:
            raise ValueError("Incorrect number of cards for rows.")
        all_c: List[int] = []; new_r: Dict[str, List[Optional[int]]] = {}
        for rn, cl in [('top',top), ('middle',middle), ('bottom',bottom)]:
            vr: List[Optional[int]] = []
            for i, ci in enumerate(cl):
                if not (isinstance(ci, int) and ci != INVALID_CARD and ci > 0): raise ValueError(f"Invalid card '{ci}' in {rn}[{i}].")
                vr.append(ci); all_c.append(ci)
            new_r[rn] = vr
        if len(all_c) != len(set(all_c)):
            counts = Counter(all_c); dups = [Card.to_str(c) for c, ct in counts.items() if ct > 1]
            raise ValueError(f"Duplicate cards in set_full_board: {dups}")
        self.rows = new_r; self._cards_placed = self.TOTAL_CAPACITY; self.is_foul = False

    def get_row_cards(self, row_name: str) -> List[int]:
        if row_name not in self.rows: return []
        return [c for c in self.rows[row_name] if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]

    def get_all_cards(self) -> Set[int]:
        all_c_set: Set[int] = set()
        for row_n in self.ROW_NAMES: all_c_set.update(self.get_row_cards(row_n))
        return all_c_set

    def get_available_slots(self) -> List[Tuple[str, int]]:
        return [(r, i) for r in self.ROW_NAMES for i, c_val in enumerate(self.rows[r]) if c_val is None]

    def get_total_cards(self) -> int: return self._cards_placed
    def is_complete(self) -> bool: return self._cards_placed == self.TOTAL_CAPACITY

    def get_board_state_tuple(self) -> Tuple[Tuple[Optional[int], ...], ...]:
        return tuple(tuple(self.rows[r_name]) for r_name in self.ROW_NAMES) # Сохраняем порядок None

    def copy(self) -> 'PlayerBoard':
        nb = PlayerBoard(); nb.rows = copy.deepcopy(self.rows) # Глубокая копия рядов
        nb._cards_placed = self._cards_placed; nb.is_foul = self.is_foul
        return nb

    def __str__(self) -> str:
        s_out = ""
        for r_n in self.ROW_NAMES:
            row_s_list = Card.hand_to_str(self.rows[r_n])
            s_out += f"{r_n.upper():<6}: " + " ".join(f"{c_s_val:^2}" for c_s_val in row_s_list) + "\n"
        s_out += f"Cards: {self._cards_placed}/{self.TOTAL_CAPACITY}, Foul: {self.is_foul}"
        return s_out.strip()
    def __repr__(self) -> str: return f"PlayerBoard(Cards={self._cards_placed}, Foul={self.is_foul})"
