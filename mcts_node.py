# mcts_node.py v2.7 (Advanced Heuristic Rollout v2 - Wheel, FL Safety)
# ИСПРАВЛЕНО: TypeError в _estimate_row_potential
# ИСПРАВЛЕНО: Вызов get_hand_rank_safe с неверным числом карт в _score_placement_v2
"""
Представление узла дерева MCTS для задачи размещения НАБОРА карт OFC Pineapple.
Использует ПРОДВИНУТУЮ ЭВРИСТИЧЕСКУЮ симуляцию v2:
- Улучшено распознавание стрит-дро (колесо, гэпы).
- Добавлена продвинутая проверка безопасности Fantasyland.
- Использует RAVE и PW.
"""

import math
import time
import random
import multiprocessing # Оставил, т.к. run_parallel_rollout его использует
import traceback
import sys
import logging
from typing import Optional, Any, List, Tuple, Set, Dict, Generator
from itertools import combinations, permutations
from collections import Counter

# Импорты из локальных модулей
try:
    # Убедимся, что импортируем все необходимое из ofc_logic
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS, INT_RANKS, PRIMES
    # Импортируем все необходимое из ofc_evaluators
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, MAX_HIGH_CARD_5,
        RANK_QUEEN, RANK_KING, RANK_ACE, # Добавлены RANK_KING, RANK_ACE
        evaluate_3_card_ofc, # Нужен для FL эвристики
        evaluator_5card # Нужен для оценки рядов
    )
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/ofc_evaluators in mcts_node.py: {e}")
    # Заглушки для возможности анализа без ofc_logic/ofc_evaluators
    class PlayerBoard:
        ROW_NAMES = ['top', 'middle', 'bottom']; ROW_CAPACITY = {'top': 3, 'middle': 5, 'bottom': 5}
        def __init__(self): self.rows = {r:[] for r in self.ROW_NAMES}; self._cards_placed = 0; self.is_foul = False
        def add_card(self, c, r, i): return False
        def get_row_cards(self, rn): return []
        def get_all_cards(self): return set()
        def get_available_slots(self): return []
        def get_total_cards(self): return 0
        def is_complete(self): return False
        def get_board_state_tuple(self): return tuple()
        def copy(self): return PlayerBoard()
    class Card:
        @staticmethod
        def get_rank_int(c): return 0
        @staticmethod
        def get_suit_int(c): return 0
        @staticmethod
        def hand_to_int(cs): return []
        @staticmethod
        def hand_to_str(ci): return []
        @staticmethod
        def to_str(c): return "??"
    class Deck: FULL_DECK_CARDS = set()
    def get_row_royalty(*args): return 0
    def check_board_foul(*args): return False
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    def evaluate_3_card_ofc(*args): return (999, "Error", "ERR")
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    WORST_RANK = 9999; WORST_CLASS = 9; MAX_HIGH_CARD_5 = 7462
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {}; STR_RANKS = ""; RANK_QUEEN = 10; RANK_KING = 11; RANK_ACE = 12
    INT_RANKS = range(13); PRIMES = []
    raise ImportError("Missing core logic/evaluator modules for MCTSNode") from e

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING) # Устанавливаем WARNING по умолчанию
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- Константы ---
FANTASY_BONUS = 70.0 # Итоговый бонус за попадание в FL
RAVE_K = 500.0       # Параметр для RAVE (AMAF)
PW_C = 2.0           # Параметр Progressive Widening C
PW_ALPHA = 0.5       # Параметр Progressive Widening Alpha

# --- Константы для Эвристики v2.7 ---
HEURISTIC_FOUL_PENALTY = -10000.0 # Очень большой штраф за фол в симуляции
HEURISTIC_FL_QUALIFY_BONUS = 25.0 # Эвристический бонус за квалификацию на FL (QQ+ или Trips на топе)
HEURISTIC_FL_REPEAT_BONUS = 10.0  # Эвристический доп. бонус за потенциальный репит (Trips top / 4oak+ bot)
HEURISTIC_FL_RISK_PENALTY_FACTOR = -2.0 # Множитель штрафа за риск фола при попытке собрать FL

# Веса для оценки рядов в эвристике
ROW_COMPLETED_HAND_WEIGHT = 1.0 # Вес силы готовой руки (можно использовать роялти)
ROW_FLUSH_DRAW_OUT_WEIGHT = 0.8 # Вес за каждый аут на флеш
ROW_STRAIGHT_DRAW_OUT_WEIGHT = 0.6 # Вес за каждый аут на стрит (OESD)
ROW_GUTSHOT_DRAW_OUT_WEIGHT = 0.3 # Вес за аут на гатшот (внутренний стрит-дро)
ROW_PAIR_OUTS_WEIGHT = 0.4 # Вес за аут на трипс с имеющейся пары
ROW_TRIPS_OUTS_WEIGHT = 0.5 # Вес за аут на каре/фулл-хаус с имеющегося трипса
ROW_HIGH_CARD_WEIGHT = 0.01 # Небольшой вес для старших карт в ряду

# --- Воркер для параллельного роллаута ---
def run_parallel_rollout(board_dict: dict, remaining_deck_ints: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Выполняет один ПРОДВИНУТЫЙ ЭВРИСТИЧЕСКИЙ роллаут (v2.7) из заданного состояния доски.
    Эта функция запускается в отдельном процессе.
    Возвращает (итоговое роялти + бонус FL, список сделанных ходов в симуляции).
    """
    try:
        # Воссоздаем объекты из словаря и списка (необходимо для multiprocessing)
        board = PlayerBoard()
        board.rows = {r: Card.hand_to_int(cards) for r, cards in board_dict.get('rows', {}).items()}
        board._cards_placed = board_dict.get('_cards_placed', 0)
        remaining_deck = set(remaining_deck_ints)

        # Вызываем статическую эвристическую симуляцию
        final_score, actions_history = MCTSNode.heuristic_rollout_simulation_v2(board, remaining_deck)

        return final_score, actions_history
    except Exception as e:
        # Логгируем ошибку в воркере
        print(f"[Worker Error] Error in parallel advanced heuristic rollout: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 0.0, [] # Возвращаем 0 в случае ошибки

# --- Класс MCTSNode ---
class MCTSNode:
    """
    Узел в дереве поиска Монте-Карло (MCTS) для размещения НАБОРА карт OFC.
    Использует продвинутую эвристическую симуляцию (v2.7), RAVE и Progressive Widening (PW).
    """
    def __init__(self,
                 board: PlayerBoard,
                 remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 placement_info: Optional[Dict[str, Any]] = None):
        """
        Инициализация узла MCTS.

        Args:
            board: Текущее состояние доски PlayerBoard.
            remaining_deck: Множество карт (int), оставшихся в колоде с точки зрения этого узла.
            parent: Родительский узел (None для корня).
            placement_info: Информация о размещении, которое привело к этому узлу
                             (формат {'placements': [(card, row, idx), ...], 'discarded': card/None}).
        """
        self.board: PlayerBoard = board
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info # Ход, который привел сюда

        # Структуры для MCTS
        self.children: Dict[Tuple[Tuple[int, str, int], ...], 'MCTSNode'] = {} # Дочерние узлы {ключ_размещения: узел}
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Optional[int]]]] = None # Неиспробованные состояния для расширения
        # Словарь для связи сгенерированных состояний с информацией о размещении (для PW и RAVE)
        self._generated_states_for_expand: Dict[Tuple[Tuple[int, str, int], ...], Tuple[PlayerBoard, Optional[int], Dict[str, Any]]] = {}

        # Статистика MCTS
        self.visits: int = 0          # Количество посещений узла (стандартный MCTS)
        self.total_reward: float = 0.0 # Суммарная награда (стандартный MCTS)

        # Статистика RAVE (Rapid Action Value Estimation / AMAF - All Moves As First)
        self.rave_visits: int = 0     # Количество посещений действий этого узла в симуляциях ниже по дереву
        self.rave_reward: float = 0.0 # Суммарная награда для RAVE

    def is_terminal(self) -> bool:
        """Проверяет, является ли узел терминальным (доска заполнена)."""
        return self.board.is_complete()

    def _generate_next_states(self, cards_dealt_for_next_street: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        """
        Генерирует все возможные следующие состояния доски после размещения
        заданного набора карт. Используется для фазы расширения (Expansion).
        Возвращает список кортежей (новая_доска, сброшенная_карта).
        Также заполняет self._generated_states_for_expand для связи с placement_info.
        """
        possible_states_data = []
        self._generated_states_for_expand.clear() # Очищаем перед генерацией

        if self.is_terminal() or not cards_dealt_for_next_street:
            return [] # Нечего генерировать

        num_dealt = len(cards_dealt_for_next_street)
        num_on_board = self.board.get_total_cards()

        # Определяем, сколько карт ставить и сбрасывать
        if num_on_board == 0: # Первая улица
            num_to_place = 5; num_to_discard = 0
            if num_dealt != 5: logger.error(f"Generate states: Expected 5 cards for street 1, got {num_dealt}"); return []
        else: # Улицы 2-5
            num_to_place = 2; num_to_discard = 1
            if num_dealt != 3: logger.error(f"Generate states: Expected 3 cards for streets 2-5, got {num_dealt}"); return []

        available_slots = self.board.get_available_slots()
        if len(available_slots) < num_to_place:
            logger.warning(f"Generate states: Not enough slots ({len(available_slots)}) to place {num_to_place} cards.")
            return [] # Некуда ставить

        # --- Итерация по вариантам сброса (если нужно) ---
        combo_iterable: Any
        if num_to_discard == 0:
            # На первой улице нет сброса
            cards_to_place_tuple = tuple(cards_dealt_for_next_street)
            combo_iterable = [(cards_to_place_tuple, None)]
        else:
            # На улицах 2-5 генерируем комбинации (2 для размещения, 1 для сброса)
            def gen_place_discard_combos():
                for combo in combinations(cards_dealt_for_next_street, num_to_place):
                    discard_list = [c for c in cards_dealt_for_next_street if c not in combo]
                    discard = discard_list[0] if discard_list else None
                    if discard is None: continue # Должна быть одна карта для сброса
                    yield tuple(combo), discard
            combo_iterable = gen_place_discard_combos()

        # --- Итерация по комбинациям слотов и перестановок карт ---
        for cards_to_place_tuple, current_discarded_card in combo_iterable:
            for slot_combination in combinations(available_slots, num_to_place):
                for card_permutation in permutations(cards_to_place_tuple):
                    try:
                        next_board = self.board.copy() # Создаем копию для каждого варианта
                        valid_placement = True
                        placements_made: List[Tuple[int, str, int]] = []

                        # Применяем размещение к копии доски
                        for i in range(num_to_place):
                            card = card_permutation[i]
                            row, idx = slot_combination[i]
                            if not next_board.add_card(card, row, idx):
                                valid_placement = False; break # Ошибка добавления (не должно быть)
                            placements_made.append((card, row, idx))

                        if valid_placement:
                            # Создаем уникальный ключ для этого размещения (для children и RAVE)
                            # Ключ - отсортированный кортеж размещений
                            placement_key = tuple(sorted(placements_made))
                            placement_info = {'placements': placements_made, 'discarded': current_discarded_card}

                            # Сохраняем связь ключа с результатом и информацией о ходе
                            if placement_key not in self._generated_states_for_expand:
                                 self._generated_states_for_expand[placement_key] = (next_board, current_discarded_card, placement_info)
                                 # Добавляем состояние в список для untried_next_states
                                 possible_states_data.append((next_board, current_discarded_card))

                    except Exception as e_perm:
                        logger.error(f"Error generating placement permutation: {e_perm}", exc_info=True)

        # Возвращаем уникальные состояния (доска, сброс) в случайном порядке
        unique_next_states = list({state_tuple: None for state_tuple in possible_states_data}.keys())
        random.shuffle(unique_next_states)
        return unique_next_states


    def expand(self) -> Optional['MCTSNode']:
        """
        Расширяет узел, создавая дочерний узел для одного неиспробованного состояния,
        ЕСЛИ это разрешено правилом Progressive Widening (PW).
        """
        if self.is_terminal(): return None # Нельзя расширять терминальный узел
        if self.untried_next_states is None:
            logger.error("Expand called before _generate_next_states was called or after it was exhausted.")
            return None
        if not self.untried_next_states:
            # logger.debug(f"Expand called on node {self} with no untried states left.")
            return None # Нет состояний для расширения

        # --- Проверка Progressive Widening (PW) ---
        num_children = len(self.children)
        # Используем self.visits + 1, чтобы разрешить расширение с самого начала
        # и избежать деления на ноль в логарифме UCT при первом посещении
        allowed_children = PW_C * math.pow(self.visits + 1, PW_ALPHA)

        if num_children >= allowed_children:
            # Лимит PW достигнут, не расширяем дальше на этой итерации
            # logger.debug(f"PW limit reached for node {self}: children={num_children}, allowed={allowed_children:.2f}. Not expanding.")
            return None # Возвращаем None, сигнализируя, что расширение не произошло
        # --- Конец проверки PW ---

        # Если PW позволяет, берем одно состояние из списка неиспробованных
        state_to_expand = self.untried_next_states.pop()
        board_state, discarded_card = state_to_expand
        board_state_tuple = board_state.get_board_state_tuple() # Для поиска ключа

        # Ищем соответствующий ключ размещения и placement_info в _generated_states_for_expand
        found_key = None
        placement_info = None
        for key, (board, discard, info) in self._generated_states_for_expand.items():
             # Сравниваем кортежное представление доски и сброшенную карту
             if board.get_board_state_tuple() == board_state_tuple and discard == discarded_card:
                 found_key = key
                 placement_info = info
                 break

        if found_key is None or placement_info is None:
             logger.error(f"Could not find matching key/info for state to expand: {state_to_expand}")
             # Попробуем следующее состояние, если есть, иначе вернем None
             return self.expand() if self.untried_next_states else None

        # Создаем новый дочерний узел
        try:
            # Передаем новую доску, тот же оставшийся deck (он обновится ниже по дереву),
            # ссылку на себя (parent) и информацию о размещении.
            child_node = MCTSNode(
                board=board_state,
                remaining_deck=self.remaining_deck, # Колода та же, карты из нее будут браться в след. _generate_next_states
                parent=self,
                placement_info=placement_info
            )
            # Добавляем дочерний узел в словарь children родителя
            self.children[found_key] = child_node
            # logger.debug(f"Expanded node with key: {found_key} (PW allowed: {num_children+1}/{allowed_children:.2f})")
            return child_node
        except Exception as e:
            logger.error(f"Error during node expansion for state key {found_key}: {e}", exc_info=True)
            # Если создание узла не удалось, попробуем следующее состояние
            return self.expand() if self.untried_next_states else None

    # === Новые Вспомогательные Функции для Эвристики v2.7 ===

    @staticmethod
    def _count_outs(needed_cards: Set[int], remaining_deck: Set[int]) -> int:
        """Считает количество аутов в оставшейся колоде."""
        return len(needed_cards.intersection(remaining_deck))

    @staticmethod
    def _detect_flush_draw(cards: List[int]) -> Tuple[Optional[int], int]:
        """Определяет масть флеш-дро и количество карт этой масти."""
        if not cards: return None, 0
        suits = Counter(Card.get_suit_int(c) for c in cards)
        for suit, count in suits.items():
            if count >= 3: # Считаем дро от 3 карт
                return suit, count
        return None, 0

    @staticmethod
    def _get_flush_draw_outs(target_suit: int, board_cards: Set[int], remaining_deck: Set[int]) -> Set[int]:
        """Находит ауты на флеш указанной масти."""
        outs = set()
        for card in remaining_deck:
            if Card.get_suit_int(card) == target_suit and card not in board_cards:
                outs.add(card)
        return outs

    @staticmethod
    def _detect_straight_draw(cards: List[int]) -> Tuple[int, Set[int]]:
        """
        (УЛУЧШЕНО v2.7) Определяет тип стрит-дро и необходимые ауты (ранги).
        Учитывает "колесо" (A-5) и внутренние гэпы.
        Возвращает: (тип_дро, {ауты_ранги}). Типы: 0=нет, 1=гатшот, 2=OESD.
        """
        if len(cards) < 3: return 0, set()

        ranks = sorted(list(set(Card.get_rank_int(c) for c in cards)))
        rank_set = set(ranks)
        n = len(ranks)
        needed_ranks = set()
        draw_type = 0 # 0: None, 1: Gutshot, 2: OESD

        # --- Явная проверка на Колесо (A-2-3-4-5) ---
        # Ранги для колеса: A=12, 2=0, 3=1, 4=2, 5=3
        wheel_ranks = {0, 1, 2, 3, 12}
        present_wheel_ranks = rank_set.intersection(wheel_ranks)

        if len(present_wheel_ranks) >= 3: # Минимум 3 карты для дро на колесо
            if present_wheel_ranks == {0, 1, 2, 12}: # A, 2, 3, 4 -> нужна 5 (ранг 3)
                needed_ranks.add(3)
                draw_type = max(draw_type, 2) # Считаем как OESD
            elif present_wheel_ranks == {0, 1, 3, 12}: # A, 2, 3, 5 -> нужна 4 (ранг 2)
                needed_ranks.add(2)
                draw_type = max(draw_type, 1) # Gutshot
            elif present_wheel_ranks == {0, 2, 3, 12}: # A, 2, 4, 5 -> нужна 3 (ранг 1)
                needed_ranks.add(1)
                draw_type = max(draw_type, 1) # Gutshot
            elif present_wheel_ranks == {1, 2, 3, 12}: # A, 3, 4, 5 -> нужна 2 (ранг 0)
                needed_ranks.add(0)
                draw_type = max(draw_type, 1) # Gutshot
            elif present_wheel_ranks == {0, 1, 2, 3}: # 2, 3, 4, 5 -> нужна A (ранг 12) и 6 (ранг 4)
                needed_ranks.add(12)
                needed_ranks.add(4) # Ранг 6
                draw_type = max(draw_type, 2) # OESD
            # Случаи с 3 картами для колеса (например, A23 -> нужны 4,5)
            elif len(present_wheel_ranks) == 3:
                 if {0,1,12}.issubset(present_wheel_ranks): # A23 -> need 4,5
                     needed_ranks.add(2); needed_ranks.add(3)
                     draw_type = max(draw_type, 1) # Double Gutshot? Считаем как Gutshot
                 elif {0,2,12}.issubset(present_wheel_ranks): # A24 -> need 3,5
                     needed_ranks.add(1); needed_ranks.add(3)
                     draw_type = max(draw_type, 1)
                 elif {0,3,12}.issubset(present_wheel_ranks): # A25 -> need 3,4
                     needed_ranks.add(1); needed_ranks.add(2)
                     draw_type = max(draw_type, 1)
                 elif {1,2,12}.issubset(present_wheel_ranks): # A34 -> need 2,5
                     needed_ranks.add(0); needed_ranks.add(3)
                     draw_type = max(draw_type, 1)
                 elif {1,3,12}.issubset(present_wheel_ranks): # A35 -> need 2,4
                     needed_ranks.add(0); needed_ranks.add(2)
                     draw_type = max(draw_type, 1)
                 elif {2,3,12}.issubset(present_wheel_ranks): # A45 -> need 2,3
                     needed_ranks.add(0); needed_ranks.add(1)
                     draw_type = max(draw_type, 1)
                 elif {0,1,2}.issubset(present_wheel_ranks): # 234 -> need A,5
                     needed_ranks.add(12); needed_ranks.add(3)
                     draw_type = max(draw_type, 1) # Double Gutshot
                 elif {0,1,3}.issubset(present_wheel_ranks): # 235 -> need 4
                     needed_ranks.add(2)
                     draw_type = max(draw_type, 1) # Gutshot
                 elif {0,2,3}.issubset(present_wheel_ranks): # 245 -> need 3
                     needed_ranks.add(1)
                     draw_type = max(draw_type, 1) # Gutshot
                 elif {1,2,3}.issubset(present_wheel_ranks): # 345 -> need 2, 6
                     needed_ranks.add(0); needed_ranks.add(4)
                     draw_type = max(draw_type, 1) # Double Gutshot
        # --- Конец проверки на Колесо ---

        # --- Проверка стандартных стритов (если не нашли дро на колесо или оно слабое) ---
        # Ищем самую длинную последовательность без разрывов > 1
        # Перебираем все возможные стартовые точки для стрита
        for start_rank in range(RANK_ACE, 3, -1): # От A до 5 (для A-5 уже проверили)
            potential_straight = set(range(start_rank - 4, start_rank + 1))
            present_ranks = rank_set.intersection(potential_straight)
            missing_ranks = potential_straight - present_ranks

            if len(present_ranks) >= 3: # Нужно хотя бы 3 карты для дро
                if len(missing_ranks) == 0: continue # Это уже готовый стрит
                elif len(missing_ranks) == 1: # Нужна 1 карта
                    needed = missing_ranks.pop()
                    # Проверяем, это OESD или Gutshot
                    if needed == start_rank - 4 or needed == start_rank: # Нужна карта с края
                        draw_type = max(draw_type, 2) # OESD
                    else: # Нужна карта внутри
                        draw_type = max(draw_type, 1) # Gutshot
                    needed_ranks.add(needed)
                elif len(missing_ranks) == 2 and len(present_ranks) == 3: # Нужны 2 карты (Double Gutshot)
                    # Пример: 8, T, J -> нужны 7 и 9
                    # Пример: 7, 8, J -> нужны 9 и T
                    needed_ranks.update(missing_ranks)
                    draw_type = max(draw_type, 1) # Считаем как Gutshot (или Double Gutshot)

        # Убираем ранги, которые уже есть на руках (на всякий случай)
        needed_ranks.difference_update(rank_set)

        # Корректируем тип, если аутов нет
        if not needed_ranks:
            draw_type = 0

        return draw_type, needed_ranks


    @staticmethod
    def _get_straight_draw_outs(needed_ranks: Set[int], board_cards: Set[int], remaining_deck: Set[int]) -> Set[int]:
        """Находит ауты на стрит по нужным рангам."""
        outs = set()
        for card in remaining_deck:
            if Card.get_rank_int(card) in needed_ranks and card not in board_cards:
                outs.add(card)
        return outs

    @staticmethod
    def _estimate_row_potential(row_name: str, row_cards: List[int], board_cards_after: Set[int], remaining_deck: Set[int]) -> float: # ИЗМЕНЕНА СИГНАТУРА
        """
        (НОВЫЙ ХЕЛПЕР v2.7) Оценивает потенциал *незавершенного* ряда.
        Используется для проверки безопасности Fantasyland.
        """
        potential_score = 0.0
        num_cards_in_row = len(row_cards)

        # --- ИСПРАВЛЕНО: Определение row_capacity ---
        if row_name not in PlayerBoard.ROW_CAPACITY:
            logger.error(f"Invalid row_name '{row_name}' in _estimate_row_potential.")
            return 0.0
        row_capacity = PlayerBoard.ROW_CAPACITY[row_name]
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

        if num_cards_in_row == 0: return 0.0
        if num_cards_in_row == row_capacity:
            # Если ряд завершен, его потенциал = его текущей силе
            rank, _, _ = get_hand_rank_safe(row_cards)
            if rank != WORST_RANK:
                 # Используем инвертированный ранг как меру (меньше = лучше)
                 potential_score = (WORST_RANK - rank) * 0.1 # Масштабируем
            return potential_score

        # Оценка незавершенного ряда
        # a) Флеш-дро
        f_suit, f_count = MCTSNode._detect_flush_draw(row_cards)
        if f_suit is not None:
            needed_flush_cards = MCTSNode._get_flush_draw_outs(f_suit, board_cards_after, remaining_deck)
            f_outs = len(needed_flush_cards)
            potential_score += f_outs * ROW_FLUSH_DRAW_OUT_WEIGHT

        # b) Стрит-дро (используем улучшенную функцию)
        s_type, s_needed_ranks = MCTSNode._detect_straight_draw(row_cards)
        if s_type > 0:
            needed_straight_cards = MCTSNode._get_straight_draw_outs(s_needed_ranks, board_cards_after, remaining_deck)
            s_outs = len(needed_straight_cards)
            if s_type == 2: potential_score += s_outs * ROW_STRAIGHT_DRAW_OUT_WEIGHT # OESD
            elif s_type == 1: potential_score += s_outs * ROW_GUTSHOT_DRAW_OUT_WEIGHT # Gutshot

        # c) Пары/Трипсы
        ranks = [Card.get_rank_int(c) for c in row_cards]
        rank_counts = Counter(ranks)
        for r, count in rank_counts.items():
            if count == 2: # Есть пара
                needed_pair_cards = {c for c in remaining_deck if Card.get_rank_int(c) == r and c not in board_cards_after}
                pair_outs = len(needed_pair_cards)
                potential_score += pair_outs * ROW_PAIR_OUTS_WEIGHT
            elif count == 3: # Есть трипс
                needed_trips_cards = {c for c in remaining_deck if Card.get_rank_int(c) == r and c not in board_cards_after}
                trips_outs = len(needed_trips_cards)
                potential_score += trips_outs * ROW_TRIPS_OUTS_WEIGHT

        # d) Старшие карты
        for card in row_cards:
            potential_score += Card.get_rank_int(card) * ROW_HIGH_CARD_WEIGHT

        return potential_score


    @staticmethod
    def _score_placement_v2(board: PlayerBoard, placement_info: Dict[str, Any], remaining_deck: Set[int]) -> float:
        """
        (УЛУЧШЕНО v2.7) Оценивает гипотетическое размещение,
        включая продвинутую проверку безопасности Fantasyland.
        """
        score = 0.0
        temp_board = board.copy()
        placements = placement_info.get('placements', [])

        # 1. Применяем размещение
        valid_placement = True
        for card, row, idx in placements:
            if not temp_board.add_card(card, row, idx):
                valid_placement = False; break
        if not valid_placement: return HEURISTIC_FOUL_PENALTY - 1000 # Ошибка

        # 2. Проверка на фол
        is_foul = False
        if temp_board.is_complete(): # Если доска полная, используем стандартную проверку
            is_foul = check_board_foul(temp_board)
        else:
            # --- ИСПРАВЛЕНО: Логика проверки фола для неполных досок ---
            try:
                top_cards = temp_board.get_row_cards("top")
                mid_cards = temp_board.get_row_cards("middle")
                bot_cards = temp_board.get_row_cards("bottom")

                rank_t, class_t = (WORST_RANK, WORST_CLASS)
                if len(top_cards) == PlayerBoard.ROW_CAPACITY['top']:
                    rank_t, class_t, _ = get_hand_rank_safe(top_cards)

                rank_m, class_m = (WORST_RANK, WORST_CLASS)
                if len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle']:
                    rank_m, class_m, _ = get_hand_rank_safe(mid_cards)

                rank_b, class_b = (WORST_RANK, WORST_CLASS)
                if len(bot_cards) == PlayerBoard.ROW_CAPACITY['bottom']:
                    rank_b, class_b, _ = get_hand_rank_safe(bot_cards)
                
                # Проверка фола между топ и мид, ТОЛЬКО ЕСЛИ ОБА ЗАПОЛНЕНЫ
                if len(top_cards) == PlayerBoard.ROW_CAPACITY['top'] and \
                   len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle']:
                    if rank_t != WORST_RANK and rank_m != WORST_RANK:
                        if (class_t < class_m) or (class_t == class_m and rank_t < rank_m):
                            is_foul = True
                
                # Проверка фола между мид и бот, ТОЛЬКО ЕСЛИ ОБА ЗАПОЛНЕНЫ
                if not is_foul and \
                   len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle'] and \
                   len(bot_cards) == PlayerBoard.ROW_CAPACITY['bottom']:
                     if rank_m != WORST_RANK and rank_b != WORST_RANK:
                        if (class_m < class_b) or (class_m == class_b and rank_m < rank_b):
                            is_foul = True
            except Exception as e_foul_check:
                logger.warning(f"Exception during partial foul check: {e_foul_check}")
                is_foul = True 
            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        if is_foul: return HEURISTIC_FOUL_PENALTY


        # 3. Оценка каждого ряда
        board_cards_after = temp_board.get_all_cards()
        row_scores = {} # Сохраним оценки для проверки FL
        is_fl_qualify_hand_placed = False
        fl_top_strength_score = 0.0

        for row_name in PlayerBoard.ROW_NAMES:
            row_cards = temp_board.get_row_cards(row_name)
            num_cards_in_row = len(row_cards)
            row_capacity = PlayerBoard.ROW_CAPACITY[row_name]
            current_row_score = 0.0
            row_strength_score = -1.0 # Сила готовой руки (или потенциал)

            if num_cards_in_row == 0:
                row_scores[row_name] = 0.0
                continue

            # Оценка завершенного ряда
            if num_cards_in_row == row_capacity:
                rank, hand_class, type_str = get_hand_rank_safe(row_cards)
                if rank != WORST_RANK:
                    royalty = get_row_royalty(row_cards, row_name)
                    current_row_score += royalty * ROW_COMPLETED_HAND_WEIGHT
                    row_strength_score = (WORST_RANK - rank) * 0.1 # Сила готовой руки

                    if row_name == "top":
                        is_fl_qualify = False
                        if hand_class == 6: is_fl_qualify = True # Trips
                        elif hand_class == 8: # Pair
                            ranks_in_top = [Card.get_rank_int(c) for c in row_cards]
                            pair_rank_val = next((r for r, count in Counter(ranks_in_top).items() if count == 2), -1)
                            if pair_rank_val >= RANK_QUEEN: is_fl_qualify = True
                        if is_fl_qualify:
                            is_fl_qualify_hand_placed = True
                            fl_top_strength_score = row_strength_score
                            current_row_score += HEURISTIC_FL_QUALIFY_BONUS
                    elif row_name == "bottom" and hand_class <= 2: # Каре и выше на боттоме
                         current_row_score += HEURISTIC_FL_REPEAT_BONUS
            # Оценка незавершенного ряда
            else:
                # --- ИСПРАВЛЕНО: Передаем row_name ---
                potential_score = MCTSNode._estimate_row_potential(row_name, row_cards, board_cards_after, remaining_deck)
                # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
                current_row_score += potential_score
                row_strength_score = potential_score # Используем потенциал как меру силы

            row_scores[row_name] = row_strength_score # Сохраняем силу/потенциал
            score += current_row_score # Добавляем оценку ряда к общему счету

        # --- Продвинутая проверка безопасности Fantasyland ---
        if is_fl_qualify_hand_placed:
            potential_middle = row_scores.get("middle", -1.0)
            potential_bottom = row_scores.get("bottom", -1.0)
            risk_penalty = 0.0

            # Штрафуем, если потенциал миддла опасно близок/выше силы топа
            if potential_middle >= fl_top_strength_score * 0.8:
                risk_penalty += (potential_middle - fl_top_strength_score * 0.8) * HEURISTIC_FL_RISK_PENALTY_FACTOR

            # Штрафуем, если потенциал боттома опасно близок/выше потенциала миддла
            if potential_bottom >= potential_middle * 1.0:
                 risk_penalty += (potential_bottom - potential_middle * 1.0) * HEURISTIC_FL_RISK_PENALTY_FACTOR

            if risk_penalty < 0:
                # logger.debug(f"FL Risk Penalty Applied: {risk_penalty:.2f} (TopStr: {fl_top_strength_score:.2f}, MidPot: {potential_middle:.2f}, BotPot: {potential_bottom:.2f})")
                score += risk_penalty # Применяем штраф

        return score


    @staticmethod
    def _choose_best_heuristic_placement_v2(
            board: PlayerBoard,
            cards_dealt: List[int],
            remaining_deck: Set[int]) -> Optional[Dict[str, Any]]:
        """
        (Продвинутая Эвристика v2.7) Выбирает лучшее размещение,
        перебирая все варианты и оценивая их с помощью _score_placement_v2.
        """
        num_on_board = board.get_total_cards()
        num_to_place = 5 if num_on_board == 0 else 2
        num_to_discard = 0 if num_on_board == 0 else 1
        available_slots = board.get_available_slots()
        if len(available_slots) < num_to_place: return None
        best_placement_info: Optional[Dict[str, Any]] = None
        best_score = -float('inf') - 1.0 
        cards_to_place_options: List[Tuple[List[int], Optional[int]]] = []
        if num_to_discard == 0:
            if len(cards_dealt) == num_to_place: cards_to_place_options.append((cards_dealt, None))
            else: return None
        else:
            if len(cards_dealt) != 3: return None
            for i in range(3):
                discard_card = cards_dealt[i]
                place_cards = [cards_dealt[j] for j in range(3) if i != j]
                cards_to_place_options.append((place_cards, discard_card))

        generated_count = 0
        evaluated_count = 0
        MAX_PERMUTATIONS = 24 
        MAX_SLOT_COMBINATIONS = 100 

        for cards_to_place, discarded_card in cards_to_place_options:
            perm_count = 0
            for card_permutation in permutations(cards_to_place):
                perm_count += 1
                if perm_count > MAX_PERMUTATIONS: break 

                slot_combo_count = 0
                for slot_combination in combinations(available_slots, num_to_place):
                    slot_combo_count += 1
                    if slot_combo_count > MAX_SLOT_COMBINATIONS: break 

                    generated_count += 1
                    current_placements: List[Tuple[int, str, int]] = []
                    for i in range(num_to_place):
                        current_placements.append((card_permutation[i], slot_combination[i][0], slot_combination[i][1]))

                    placement_info = {'placements': current_placements, 'discarded': discarded_card}
                    score = MCTSNode._score_placement_v2(board, placement_info, remaining_deck)
                    evaluated_count += 1
                    
                    if score >= best_score:
                        if score > HEURISTIC_FOUL_PENALTY + 1: 
                            best_score = score
                            best_placement_info = placement_info
        
        if best_placement_info is None and evaluated_count > 0:
             logger.warning(f"Heuristic v2.7: No valid (non-foul) placement found after evaluating {evaluated_count} options. Returning None.")
             return None 

        return best_placement_info


    @staticmethod
    def heuristic_rollout_simulation_v2(
            initial_board: PlayerBoard,
            initial_remaining_deck: Set[int]) -> Tuple[float, List[Dict[str, Any]]]:
        """
        Статический метод для выполнения ПРОДВИНУТОЙ ЭВРИСТИЧЕСКОЙ симуляции (v2.7).
        """
        actions_history: List[Dict[str, Any]] = []
        try:
            current_board = initial_board.copy()
            deck_sim_list = list(initial_remaining_deck); random.shuffle(deck_sim_list)
            deck_sim_set = set(deck_sim_list)

            while not current_board.is_complete():
                num_cards_on_board = current_board.get_total_cards()
                num_to_deal = 3 if num_cards_on_board > 0 else 5
                if len(deck_sim_list) < num_to_deal:
                    return 0.0, actions_history 

                dealt_cards = [deck_sim_list.pop() for _ in range(num_to_deal)]
                deck_sim_set.difference_update(dealt_cards)

                best_action = MCTSNode._choose_best_heuristic_placement_v2(
                    current_board, dealt_cards, deck_sim_set
                )
                
                if best_action is None:
                    return 0.0, actions_history

                placements = best_action.get('placements', [])
                valid_placement = True
                for card, row, idx in placements:
                    if not current_board.add_card(card, row, idx):
                        logger.error(f"Heuristic rollout v2.7: Failed to apply chosen placement {Card.to_str(card)}@{row}[{idx}]")
                        valid_placement = False; break
                if not valid_placement:
                    return 0.0, actions_history 

                actions_history.append(best_action)

            is_foul = check_board_foul(current_board)
            if is_foul: return 0.0, actions_history

            total_royalty = sum(get_row_royalty(current_board.get_row_cards(r), r) for r in PlayerBoard.ROW_NAMES)
            final_fantasy_bonus = 0.0
            top_row_cards = current_board.get_row_cards("top")
            if len(top_row_cards) == 3:
                 rank_t, class_t, type_t = get_hand_rank_safe(top_row_cards)
                 if rank_t != WORST_RANK:
                     is_fantasy_hand = False
                     if class_t == 6: is_fantasy_hand = True # Trips
                     elif class_t == 8: # Pair
                         ranks_in_top = [Card.get_rank_int(c) for c in top_row_cards]
                         pair_rank_val = next((r for r, count in Counter(ranks_in_top).items() if count == 2), -1)
                         if pair_rank_val >= RANK_QUEEN: is_fantasy_hand = True
                     if is_fantasy_hand: final_fantasy_bonus = FANTASY_BONUS

            final_score = total_royalty + final_fantasy_bonus
            return final_score, actions_history
        except Exception as e:
            logger.error(f"Error during heuristic rollout simulation v2.7: {e}", exc_info=True)
            return 0.0, actions_history

    # --- Стандартные методы MCTS (без изменений) ---

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        """Выбирает дочерний узел с использованием формулы UCB1 + RAVE."""
        best_score = -float('inf'); best_child = None
        parent_visits = self.visits

        if parent_visits == 0 or not self.children:
            return random.choice(list(self.children.values())) if self.children else None

        parent_visits_log = math.log(parent_visits + 1e-6) 

        items = list(self.children.items()); random.shuffle(items)
        beta = math.sqrt(RAVE_K / (3 * parent_visits + RAVE_K))

        for placement_key, child in items:
            child_visits = child.visits
            score = 0.0

            if child_visits == 0:
                if child.rave_visits > 0:
                    rave_score = child.rave_reward / child.rave_visits
                    score = beta * rave_score + exploration_constant * math.sqrt(parent_visits_log / (child_visits + 1e-6)) 
                else:
                    score = 1e6 + random.random() 
            else:
                node_score = child.total_reward / child_visits 
                rave_score = child.rave_reward / child.rave_visits if child.rave_visits > 0 else node_score
                combined_score = (1.0 - beta) * node_score + beta * rave_score
                explore_term = exploration_constant * math.sqrt(parent_visits_log / child_visits)
                score = combined_score + explore_term

            if score > best_score:
                best_score = score
                best_child = child
        
        if best_child is None and items:
             logger.warning(f"UCT selection resulted in None for node {self}. Choosing random child.")
             best_child = random.choice([c for _, c in items])

        return best_child


    def backpropagate(self, reward: float):
        """Обновляет стандартную статистику MCTS (visits, total_reward) узлов вдоль пути."""
        node: Optional[MCTSNode] = self
        while node is not None:
            node.visits += 1
            node.total_reward += reward
            node = node.parent

    def backpropagate_rave(self, simulation_actions: List[Dict[str, Any]], reward: float):
        """
        Обновляет RAVE статистику (rave_visits, rave_reward) узлов вдоль пути (AMAF).
        """
        sim_action_keys: Set[Tuple[Tuple[int, str, int], ...]] = set()
        for action in simulation_actions:
            placements = action.get('placements')
            if placements:
                 try:
                     action_key = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placements]))
                     sim_action_keys.add(action_key)
                 except Exception as e:
                     logger.warning(f"RAVE Backprop Key Error creating key from action {action}: {e}")

        if not sim_action_keys: return 

        node: Optional[MCTSNode] = self
        while node is not None:
            for child_key, child_node in node.children.items():
                if child_key in sim_action_keys:
                    child_node.rave_visits += 1
                    child_node.rave_reward += reward
            node = node.parent 

    def __repr__(self):
        """Строковое представление узла для отладки."""
        q_val = self.total_reward / self.visits if self.visits > 0 else 0.0
        rave_q_val = self.rave_reward / self.rave_visits if self.rave_visits > 0 else 0.0
        action_str = "Root"
        if self.placement_info and self.placement_info.get('placements'):
             p_list = self.placement_info['placements']
             action_str = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_list])
             if self.placement_info.get('discarded'):
                 action_str += f" (D: {Card.to_str(self.placement_info['discarded'])})"
        
        untried_count = len(self.untried_next_states) if self.untried_next_states is not None else 'N/A'

        return (f"[Node V={self.visits} R={q_val:.2f} RV={self.rave_visits} RR={rave_q_val:.2f} "
                f"NChild={len(self.children)} UStates={untried_count} "
                f"Act={action_str}]")
