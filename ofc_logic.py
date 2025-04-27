# ofc_logic.py v1.2
"""
Базовая логика игры OFC Pineapple: Карты, Колода, Доска, Утилиты Подсчета.
Версия для режима тренировки (без Fantasyland, без сравнения с оппонентом).
Исправлена логика check_board_foul.
"""

import random
import copy
import logging
from typing import List, Tuple, Dict, Optional, Set, Any
from collections import Counter

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING) # Устанавливаем уровень по умолчанию

# --- Константы Карт ---
STR_RANKS: str = '23456789TJQKA'
INT_RANKS: range = range(13)
PRIMES: List[int] = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41]

RANK_CHAR_TO_INT: Dict[str, int] = {rank: i for i, rank in enumerate(STR_RANKS)}
SUIT_CHAR_TO_INT: Dict[str, int] = {'s': 1, 'h': 2, 'd': 4, 'c': 8} # Spades, Hearts, Diamonds, Clubs

INT_RANK_TO_CHAR: Dict[int, str] = {i: rank for i, rank in enumerate(STR_RANKS)}
INT_SUIT_TO_CHAR: Dict[int, str] = {1: 's', 2: 'h', 4: 'd', 8: 'c'}

RANK_MAP: Dict[str, int] = RANK_CHAR_TO_INT # Алиас

INVALID_CARD: int = -1
CARD_PLACEHOLDER: str = "__"
NUM_CARDS: int = 52

# --- Класс Card (Утилиты) ---
class Card:
    """
    Класс-обертка для статических методов работы с целочисленным представлением карт.
    Использует 32-битное представление для эффективности.
    """
    @staticmethod
    def from_str(card_str: str) -> int:
        """Преобразует строку карты (e.g., 'As') в int."""
        if not isinstance(card_str, str):
             raise TypeError(f"Input must be str, got {type(card_str)}")
        if len(card_str) != 2:
            raise ValueError(f"Invalid card string format: '{card_str}'")

        rank_char = card_str[0].upper()
        suit_char = card_str[1].lower()

        rank_int = RANK_CHAR_TO_INT.get(rank_char)
        suit_int = SUIT_CHAR_TO_INT.get(suit_char)

        if rank_int is None: raise ValueError(f"Invalid rank: '{rank_char}'")
        if suit_int is None: raise ValueError(f"Invalid suit: '{suit_char}'")

        try: rank_prime = PRIMES[rank_int]
        except IndexError: raise ValueError(f"Internal error: Prime not found for rank {rank_int}")

        bitrank = 1 << rank_int << 16
        suit = suit_int << 12
        rank = rank_int << 8
        return bitrank | suit | rank | rank_prime

    @staticmethod
    def to_str(card_int: Optional[int]) -> str:
        """Преобразует int карты обратно в строку ('As', '__')."""
        if card_int is None or card_int == INVALID_CARD or not isinstance(card_int, int) or card_int <= 0:
            return CARD_PLACEHOLDER

        rank_int = Card.get_rank_int(card_int)
        suit_int = Card.get_suit_int(card_int)

        rank_char = INT_RANK_TO_CHAR.get(rank_int)
        suit_char = INT_SUIT_TO_CHAR.get(suit_int)

        if rank_char and suit_char: return rank_char + suit_char
        else: return CARD_PLACEHOLDER

    @staticmethod
    def get_rank_int(card_int: int) -> int:
        """Извлекает индекс ранга (0-12)."""
        return (card_int >> 8) & 0xF

    @staticmethod
    def get_suit_int(card_int: int) -> int:
        """Извлекает int масти (1, 2, 4, 8)."""
        return (card_int >> 12) & 0xF

    @staticmethod
    def get_prime(card_int: int) -> int:
        """Извлекает простое число ранга."""
        return card_int & 0x3F

    @staticmethod
    def hand_to_int(card_strs: List[Optional[str]]) -> List[Optional[int]]:
        """Конвертирует список строк карт в список int."""
        hand_ints = []
        for s in card_strs:
            if s is None or s == CARD_PLACEHOLDER or not isinstance(s, str) or len(s) != 2:
                hand_ints.append(None)
            else:
                try: hand_ints.append(Card.from_str(s))
                except ValueError: hand_ints.append(None)
        return hand_ints

    @staticmethod
    def hand_to_str(card_ints: List[Optional[int]]) -> List[str]:
        """Конвертирует список int карт в список строк."""
        return [Card.to_str(c) for c in card_ints]

# --- Класс Deck ---
class Deck:
    """
    Представляет стандартную 52-карточную колоду.
    Использует set для эффективности.
    """
    _FULL_DECK_STRS: Set[str] = {r + s for r in STR_RANKS for s in SUIT_CHAR_TO_INT.keys()}
    FULL_DECK_CARDS: Set[int] = set()
    _initialization_error: bool = False

    try:
        for card_s in _FULL_DECK_STRS:
            try:
                card_int = Card.from_str(card_s)
                if not isinstance(card_int, int) or card_int == INVALID_CARD or card_int <= 0:
                    _initialization_error = True
                else: FULL_DECK_CARDS.add(card_int)
            except Exception: _initialization_error = True
        if len(FULL_DECK_CARDS) != 52 or _initialization_error:
            raise RuntimeError("Failed to initialize the standard 52-card deck.")
    except Exception as e_init:
        logger.critical(f"CRITICAL ERROR during Deck class initialization: {e_init}", exc_info=True)
        raise

    def __init__(self, cards: Optional[Set[int]] = None):
        """Инициализирует колоду (полную или из набора)."""
        if cards is None: self.cards: Set[int] = self.FULL_DECK_CARDS.copy()
        else: self.cards: Set[int] = {c for c in cards if isinstance(c, int) and c != INVALID_CARD and c > 0}

    def deal(self, n: int) -> List[int]:
        """Раздает n случайных карт."""
        if n <= 0: return []
        current_len = len(self.cards)
        num_to_deal = min(n, current_len)
        if n > current_len: logger.warning(f"Deck.deal: Trying {n}, only {current_len} left.")
        if num_to_deal == 0: return []
        try:
            card_list = list(self.cards)
            dealt_cards = random.sample(card_list, num_to_deal)
            self.cards.difference_update(dealt_cards)
            return dealt_cards
        except Exception as e: logger.error(f"ERROR in Deck.deal({n}): {e}", exc_info=True); return []

    def remove(self, cards_to_remove: List[int]):
        """Удаляет конкретные карты."""
        valid_cards_to_remove = {c for c in cards_to_remove if isinstance(c, int) and c != INVALID_CARD and c > 0}
        self.cards.difference_update(valid_cards_to_remove)

    def get_remaining_cards(self) -> List[int]:
        """Возвращает список оставшихся карт."""
        return list(self.cards)

    def copy(self) -> 'Deck':
        """Создает копию колоды."""
        return Deck(self.cards.copy())

    def __len__(self) -> int: return len(self.cards)
    def __contains__(self, card: int) -> bool:
        if not isinstance(card, int) or card == INVALID_CARD or card <= 0: return False
        return card in self.cards
    def __str__(self) -> str: return f"Deck({len(self.cards)} cards)"
    def __repr__(self) -> str: return self.__str__()

# --- Класс PlayerBoard ---
class PlayerBoard:
    """Представляет доску одного игрока (3 ряда)."""
    ROW_CAPACITY: Dict[str, int] = {'top': 3, 'middle': 5, 'bottom': 5}
    ROW_NAMES: List[str] = ['top', 'middle', 'bottom']
    TOTAL_CAPACITY: int = sum(ROW_CAPACITY.values()) # 13

    def __init__(self):
        """Инициализирует пустую доску."""
        self.rows: Dict[str, List[Optional[int]]] = {
            name: [None] * capacity for name, capacity in self.ROW_CAPACITY.items()
        }
        self._cards_placed: int = 0
        self.is_foul: bool = False # Флаг фола (устанавливается извне)

    def add_card(self, card_int: int, row_name: str, index: int) -> bool:
        """Добавляет карту в слот. Возвращает True при успехе."""
        if row_name not in self.ROW_NAMES: return False
        if not isinstance(card_int, int) or card_int == INVALID_CARD or card_int <= 0: return False
        capacity = self.ROW_CAPACITY[row_name]
        if not (0 <= index < capacity): return False
        if self.rows[row_name][index] is not None: return False # Слот занят

        self.rows[row_name][index] = card_int
        self._cards_placed += 1
        self.is_foul = False # Сбрасываем фол при изменении
        return True

    def set_full_board(self, top: List[int], middle: List[int], bottom: List[int]):
        """Устанавливает всю доску (для ФЛ или тестов)."""
        if not isinstance(top, list) or not isinstance(middle, list) or not isinstance(bottom, list):
             raise TypeError("Input rows must be lists.")
        if len(top) != 3 or len(middle) != 5 or len(bottom) != 5:
            raise ValueError("Incorrect number of cards provided.")

        all_cards: List[int] = []
        card_lists = {'top': top, 'middle': middle, 'bottom': bottom}
        for row_name, card_list in card_lists.items():
             for i, card_int in enumerate(card_list):
                  if not isinstance(card_int, int) or card_int == INVALID_CARD or card_int <= 0:
                       raise ValueError(f"Invalid card '{card_int}' in row '{row_name}'.")
                  all_cards.append(card_int)
        if len(all_cards) != len(set(all_cards)): raise ValueError("Duplicate cards provided.")

        self.rows['top'] = list(top); self.rows['middle'] = list(middle); self.rows['bottom'] = list(bottom)
        self._cards_placed = self.TOTAL_CAPACITY
        self.is_foul = False # Сбрасываем фол при установке

    def get_row_cards(self, row_name: str) -> List[int]:
        """Возвращает список валидных карт (int) в ряду."""
        if row_name not in self.rows: return []
        return [c for c in self.rows[row_name] if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]

    def get_available_slots(self) -> List[Tuple[str, int]]:
        """Возвращает список доступных слотов ('row_name', index)."""
        return [(r, i) for r in self.ROW_NAMES for i, c in enumerate(self.rows[r]) if c is None]

    def get_total_cards(self) -> int: return self._cards_placed
    def is_complete(self) -> bool: return self._cards_placed == self.TOTAL_CAPACITY

    def get_board_state_tuple(self) -> Tuple[Tuple[Optional[int], ...], ...]:
        """Возвращает неизменяемое представление доски (кортеж кортежей int)."""
        # Сортируем карты внутри каждого ряда для каноничности (None идут в конец)
        def sort_key(card_int): return (card_int is None, -Card.get_rank_int(card_int) if card_int is not None else 99)
        return tuple(tuple(sorted(self.rows[r], key=sort_key)) for r in self.ROW_NAMES)

    def copy(self) -> 'PlayerBoard':
        """Создает глубокую копию доски."""
        new_board = PlayerBoard()
        new_board.rows = {r: list(cards) for r, cards in self.rows.items()}
        new_board._cards_placed = self._cards_placed
        new_board.is_foul = self.is_foul
        return new_board

    def __str__(self) -> str:
        """Строковое представление доски."""
        s = ""
        max_len = max(len(self.rows[r_name]) for r_name in self.ROW_NAMES)
        for r_name in self.ROW_NAMES:
            row_str = [Card.to_str(c) for c in self.rows[r_name]]
            row_str += [CARD_PLACEHOLDER] * (max_len - len(row_str))
            s += f"{r_name.upper():<6}: " + " ".join(f"{c:^2}" for c in row_str) + "\n"
        s += f"Cards: {self._cards_placed}/{self.TOTAL_CAPACITY}, Foul: {self.is_foul}"
        return s.strip()

    def __repr__(self) -> str: return f"PlayerBoard(Cards={self._cards_placed}, Foul={self.is_foul})"

# --- Функции Подсчета Очков (Scoring) ---
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

def check_board_foul(board: PlayerBoard, evaluator_3card, evaluator_5card) -> bool:
    """
    Проверяет доску на фол (нарушение порядка линий).
    Требует передачи эвалюаторов.
    """
    if not board.is_complete(): return False
    try:
        top_cards = board.get_row_cards('top')
        mid_cards = board.get_row_cards('middle')
        bot_cards = board.get_row_cards('bottom')

        # Проверка на дубликаты между рядами (хотя set_full_board уже проверяет)
        all_cards = top_cards + mid_cards + bot_cards
        if len(all_cards) != len(set(all_cards)):
             logger.warning("Duplicate cards detected across rows in check_board_foul.")
             return False # Невалидная доска не фол

        # --- ИСПРАВЛЕНО: Используем raw ранги для сравнения ---
        # Получаем raw ранг для 3-карточной руки
        rank_t, _, _ = evaluator_3card(top_cards[0], top_cards[1], top_cards[2])
        # Получаем raw ранги для 5-карточных рук
        rank_m = evaluator_5card.evaluate(mid_cards)
        rank_b = evaluator_5card.evaluate(bot_cards)

        # Проверяем, что все ранги валидны (меньше WORST_RANK_5CARD)
        # WORST_RANK_3CARD = 455, WORST_RANK_5CARD = 7463
        if rank_t > WORST_RANK_3CARD or rank_m >= WORST_RANK_5CARD or rank_b >= WORST_RANK_5CARD:
             logger.warning(f"Could not determine valid ranks for foul check. T:{rank_t}, M:{rank_m}, B:{rank_b}")
             return False # Считаем не фолом при ошибке оценки

        # --- ИСПРАВЛЕНО: Логика проверки фола ---
        # Меньший ранг = лучше. Фол, если top > middle или middle > bottom
        # (т.е. ранг топа численно БОЛЬШЕ ранга мидла, или ранг мидла БОЛЬШЕ ранга боттома)
        is_foul = (rank_t > rank_m) or (rank_m > rank_b)
        return is_foul
    except Exception as e:
        logger.error(f"Error during check_board_foul: {e}", exc_info=True)
        return False # Считаем не фолом при ошибке

def get_row_royalty(cards: List[Optional[int]], row_name: str, evaluator_3card, evaluator_5card) -> int:
    """
    Вычисляет роялти для ряда, используя переданные эвалюаторы.
    """
    if not isinstance(cards, list): return 0
    valid_cards = [c for c in cards if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]
    num_cards = len(valid_cards)
    royalty = 0

    try:
        if row_name == "top":
            if num_cards != 3: return 0
            if len(valid_cards) != len(set(valid_cards)): return 0
            _, type_str, rank_str = evaluator_3card(valid_cards[0], valid_cards[1], valid_cards[2])
            if type_str == 'Trips':
                rank_index = RANK_MAP.get(rank_str[0])
                royalty = ROYALTY_TOP_TRIPS.get(rank_index, 0)
            elif type_str == 'Pair':
                rank_index = RANK_MAP.get(rank_str[0])
                royalty = ROYALTY_TOP_PAIRS.get(rank_index, 0)
            return royalty

        elif row_name in ["middle", "bottom"]:
            if num_cards != 5: return 0
            if len(valid_cards) != len(set(valid_cards)): return 0
            rank_eval = evaluator_5card.evaluate(valid_cards)
            # Проверяем валидность ранга перед использованием
            if rank_eval >= evaluator_5card.table.WORST_RANK_5CARD:
                logger.warning(f"Invalid rank {rank_eval} received for royalty calculation in row {row_name}")
                return 0
            rank_class = evaluator_5card.get_rank_class(rank_eval)
            hand_name = evaluator_5card.class_to_string(rank_class)

            # Проверка на Royal Flush (самый высокий стрит-флеш)
            is_royal = (hand_name == "Straight Flush" and
                        Card.get_rank_int(max(valid_cards, key=Card.get_rank_int)) == RANK_MAP['A'])

            table = ROYALTY_MIDDLE_POINTS if row_name == "middle" else ROYALTY_BOTTOM_POINTS

            if is_royal:
                royalty = ROYALTY_MIDDLE_POINTS.get("Royal Flush", 50) if row_name == "middle" \
                     else ROYALTY_BOTTOM_POINTS.get("Royal Flush", 25)
            else:
                royalty = table.get(hand_name, 0)

            # Трипс не дает роялти на боттоме
            if row_name == "bottom" and hand_name == "Three of a Kind":
                 royalty = 0
            return royalty
        else:
            logger.warning(f"Unknown row name '{row_name}' in get_row_royalty.")
            return 0
    except Exception as e:
        logger.error(f"Error calculating royalty for {row_name}: {e}", exc_info=True)
        return 0
