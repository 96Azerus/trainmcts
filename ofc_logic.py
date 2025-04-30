# ofc_logic.py v1.9
"""
Базовая логика игры OFC Pineapple: Карты, Колода, Доска, Константы.
Версия для режима тренировки (без Fantasyland, без сравнения с оппонентом).
Исправлена ошибка в Card.from_str (неправильный сдвиг bitrank).
Убраны функции check_board_foul, get_row_royalty и константы роялти
для разрыва циклической зависимости (перенесены в ofc_evaluators.py).
"""

import random
import copy
import logging
from typing import List, Tuple, Dict, Optional, Set, Any
# Убран импорт Counter, т.к. он больше не нужен здесь
# Убраны импорты из ofc_evaluators

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.DEBUG) # Устанавливаем DEBUG для отладки
    handler = logging.StreamHandler() # Вывод в консоль
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


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

        # Исправленный сдвиг для bitrank
        bitrank = 1 << (rank_int + 16) # Сдвигаем 1 на (rank + 16) позиций

        suit = suit_int << 12         # Shift suit int left by 12
        rank = rank_int << 8          # Shift rank int left by 8
        return bitrank | suit | rank | rank_prime

    @staticmethod
    def to_str(card_int: Optional[int]) -> str:
        """Преобразует int карты обратно в строку ('As', '__')."""
        if card_int is None or card_int == INVALID_CARD or not isinstance(card_int, int) or card_int <= 0:
            return CARD_PLACEHOLDER

        try:
            rank_int = Card.get_rank_int(card_int)
            suit_int = Card.get_suit_int(card_int)

            rank_char = INT_RANK_TO_CHAR.get(rank_int)
            suit_char = INT_SUIT_TO_CHAR.get(suit_int)

            if rank_char and suit_char: return rank_char + suit_char
            else:
                logger.warning(f"Could not convert card int {card_int} to string (rank={rank_int}, suit={suit_int})")
                return CARD_PLACEHOLDER
        except Exception as e:
            logger.error(f"Error converting card int {card_int} to string: {e}")
            return CARD_PLACEHOLDER


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
        hand_ints: List[Optional[int]] = []
        if not isinstance(card_strs, list):
            logger.warning(f"hand_to_int expected list, got {type(card_strs)}")
            # Возвращаем список None той же длины, если вход не список
            try: length = len(card_strs) # type: ignore
            except TypeError: length = 0
            return [None] * length

        for s in card_strs:
            if s is None or s == CARD_PLACEHOLDER or not isinstance(s, str) or len(s) != 2:
                hand_ints.append(None)
            else:
                try: hand_ints.append(Card.from_str(s))
                except (ValueError, TypeError) as e:
                    logger.debug(f"Invalid card string '{s}' in hand_to_int: {e}")
                    hand_ints.append(None)
        return hand_ints

    @staticmethod
    def hand_to_str(card_ints: List[Optional[int]]) -> List[str]:
        """Конвертирует список int карт в список строк."""
        if not isinstance(card_ints, list):
            logger.warning(f"hand_to_str expected list, got {type(card_ints)}")
            try: length = len(card_ints) # type: ignore
            except TypeError: length = 0
            return [CARD_PLACEHOLDER] * length
        return [Card.to_str(c) for c in card_ints]

# --- Глобальная функция card_to_str для обратной совместимости ---
def card_to_str(card_int: Optional[int]) -> str:
    """Глобальная функция-обертка для Card.to_str."""
    return Card.to_str(card_int)

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
                # Добавим проверку типа на всякий случай
                if not isinstance(card_int, int) or card_int == INVALID_CARD or card_int <= 0:
                    logger.error(f"Invalid card integer generated for '{card_s}': {card_int}")
                    _initialization_error = True
                else: FULL_DECK_CARDS.add(card_int)
            except (ValueError, TypeError) as e_card:
                 logger.error(f"Error converting card string '{card_s}' during deck init: {e_card}")
                 _initialization_error = True
        if len(FULL_DECK_CARDS) != 52 or _initialization_error:
            logger.critical(f"Deck initialization failed! Expected 52 unique cards, got {len(FULL_DECK_CARDS)}. Error flag: {_initialization_error}")
            raise RuntimeError("Failed to initialize the standard 52-card deck.")
    except Exception as e_init:
        logger.critical(f"CRITICAL ERROR during Deck class initialization: {e_init}", exc_info=True)
        # Перевыбрасываем исключение, чтобы предотвратить запуск с неполной колодой
        raise RuntimeError("Failed to initialize the standard 52-card deck.") from e_init

    def __init__(self, cards: Optional[Set[int]] = None):
        """Инициализирует колоду (полную или из набора)."""
        if cards is None:
            self.cards: Set[int] = self.FULL_DECK_CARDS.copy()
        else:
            # Фильтруем невалидные карты при инициализации из набора
            self.cards: Set[int] = {c for c in cards if isinstance(c, int) and c != INVALID_CARD and c > 0}
            if len(self.cards) != len(cards):
                logger.warning(f"Initialized deck with {len(self.cards)} cards, filtered from {len(cards)} provided.")

    def deal(self, n: int) -> List[int]:
        """Раздает n случайных карт."""
        if not isinstance(n, int) or n <= 0: return []
        current_len = len(self.cards)
        num_to_deal = min(n, current_len)
        if n > current_len: logger.warning(f"Deck.deal: Trying to deal {n}, but only {current_len} cards left.")
        if num_to_deal == 0: return []
        try:
            # random.sample работает со множествами напрямую, но требует преобразования в список/кортеж
            dealt_cards = random.sample(list(self.cards), num_to_deal)
            self.cards.difference_update(dealt_cards) # Удаляем из множества
            return dealt_cards
        except Exception as e:
            logger.error(f"ERROR in Deck.deal({n}): {e}", exc_info=True)
            return [] # Возвращаем пустой список при ошибке

    def remove(self, cards_to_remove: List[int]):
        """Удаляет конкретные карты."""
        if not isinstance(cards_to_remove, list):
            logger.warning(f"Deck.remove expected list, got {type(cards_to_remove)}")
            return
        # Фильтруем невалидные карты перед удалением
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
        self.is_foul: bool = False # Флаг фола (устанавливается извне или check_board_foul)

    def add_card(self, card_int: int, row_name: str, index: int) -> bool:
        """Добавляет карту в слот. Возвращает True при успехе."""
        if row_name not in self.ROW_NAMES:
            logger.warning(f"Invalid row name '{row_name}' in add_card.")
            return False
        if not isinstance(card_int, int) or card_int == INVALID_CARD or card_int <= 0:
            logger.warning(f"Invalid card integer '{card_int}' in add_card.")
            return False
        capacity = self.ROW_CAPACITY[row_name]
        if not (0 <= index < capacity):
            logger.warning(f"Invalid index {index} for row '{row_name}' (capacity {capacity}) in add_card.")
            return False
        if self.rows[row_name][index] is not None:
            logger.debug(f"Slot {row_name}[{index}] already occupied by {Card.to_str(self.rows[row_name][index])}.")
            return False # Слот занят

        # Проверка на дубликат на всей доске перед добавлением
        current_cards = self.get_all_cards()
        if card_int in current_cards:
            logger.warning(f"Attempted to add duplicate card {Card.to_str(card_int)} to board.")
            return False

        self.rows[row_name][index] = card_int
        self._cards_placed += 1
        self.is_foul = False # Сбрасываем флаг фола при любом успешном добавлении карты
        return True

    def set_full_board(self, top: List[int], middle: List[int], bottom: List[int]):
        """
        Устанавливает всю доску (для ФЛ или тестов).
        Проверяет корректность и уникальность карт.
        """
        if not isinstance(top, list) or not isinstance(middle, list) or not isinstance(bottom, list):
             raise TypeError("Input rows must be lists.")
        if len(top) != self.ROW_CAPACITY['top'] or \
           len(middle) != self.ROW_CAPACITY['middle'] or \
           len(bottom) != self.ROW_CAPACITY['bottom']:
            raise ValueError(f"Incorrect number of cards provided. Expected "
                             f"{self.ROW_CAPACITY['top']},{self.ROW_CAPACITY['middle']},{self.ROW_CAPACITY['bottom']}.")

        all_cards: List[int] = []
        card_lists = {'top': top, 'middle': middle, 'bottom': bottom}
        new_rows: Dict[str, List[Optional[int]]] = {}

        for row_name, card_list in card_lists.items():
             validated_row: List[Optional[int]] = []
             for i, card_int in enumerate(card_list):
                  if not isinstance(card_int, int) or card_int == INVALID_CARD or card_int <= 0:
                       raise ValueError(f"Invalid card integer '{card_int}' in row '{row_name}' at index {i}.")
                  validated_row.append(card_int)
                  all_cards.append(card_int)
             # Сохраняем как List[Optional[int]] для консистентности типа self.rows
             new_rows[row_name] = validated_row

        # --- ИСПРАВЛЕНО: Используем Counter для проверки дубликатов ---
        from collections import Counter # Импортируем Counter здесь
        if len(all_cards) != len(set(all_cards)):
            counts = Counter(all_cards)
            duplicates = [Card.to_str(c) for c, count in counts.items() if count > 1]
            raise ValueError(f"Duplicate cards provided to set_full_board: {duplicates}")

        # Если все проверки пройдены, обновляем состояние доски
        self.rows = new_rows
        self._cards_placed = self.TOTAL_CAPACITY
        self.is_foul = False # Сбрасываем фол при установке новой полной доски

    def get_row_cards(self, row_name: str) -> List[int]:
        """Возвращает список валидных карт (int) в ряду."""
        if row_name not in self.rows: return []
        # Фильтруем None и невалидные значения на всякий случай
        return [c for c in self.rows[row_name] if isinstance(c, int) and c is not None and c != INVALID_CARD and c > 0]

    def get_all_cards(self) -> Set[int]:
        """Возвращает множество всех валидных карт на доске."""
        all_c: Set[int] = set()
        for row_name in self.ROW_NAMES:
            all_c.update(self.get_row_cards(row_name))
        return all_c

    def get_available_slots(self) -> List[Tuple[str, int]]:
        """Возвращает список доступных слотов ('row_name', index)."""
        return [(r, i) for r in self.ROW_NAMES for i, c in enumerate(self.rows[r]) if c is None]

    def get_total_cards(self) -> int: return self._cards_placed
    def is_complete(self) -> bool: return self._cards_placed == self.TOTAL_CAPACITY

    def get_board_state_tuple(self) -> Tuple[Tuple[Optional[int], ...], ...]:
        """
        Возвращает неизменяемое представление доски (кортеж кортежей int).
        Карты внутри рядов НЕ сортируются, чтобы сохранить порядок размещения.
        """
        return tuple(tuple(self.rows[r]) for r in self.ROW_NAMES)

    def copy(self) -> 'PlayerBoard':
        """Создает глубокую копию доски."""
        new_board = PlayerBoard()
        # Используем copy.deepcopy для полной изоляции списков
        new_board.rows = copy.deepcopy(self.rows)
        new_board._cards_placed = self._cards_placed
        new_board.is_foul = self.is_foul
        return new_board

    def __str__(self) -> str:
        """Строковое представление доски."""
        s = ""
        # Используем ROW_CAPACITY для определения длины каждого ряда
        for r_name in self.ROW_NAMES:
            row_str = Card.hand_to_str(self.rows[r_name]) # Используем Card.hand_to_str
            s += f"{r_name.upper():<6}: " + " ".join(f"{c:^2}" for c in row_str) + "\n"
        s += f"Cards: {self._cards_placed}/{self.TOTAL_CAPACITY}, Foul: {self.is_foul}"
        return s.strip()

    def __repr__(self) -> str: return f"PlayerBoard(Cards={self._cards_placed}, Foul={self.is_foul})"

# --- Функции Подсчета Очков (Scoring) ---
# УБРАНЫ: check_board_foul, get_row_royalty и константы роялти
# Они перенесены в ofc_evaluators.py
