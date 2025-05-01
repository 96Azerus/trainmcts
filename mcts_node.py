# mcts_node.py v2.0 (Refactored for Set Placement)
"""
Представление узла дерева MCTS для задачи размещения НАБОРА карт OFC Pineapple.
Цель - максимизация роялти с учетом цели "Фантазия".
Использует UCT/UCB1. Параллельные роллауты.
"""

import math
import time
import random
import multiprocessing
import traceback
import sys
import logging
from typing import Optional, Any, List, Tuple, Set, Dict, Generator
from itertools import combinations, permutations # Добавлен permutations

# Импорты из локальных модулей (без изменений)
try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP # Добавлен RANK_MAP
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS, # Добавлен WORST_CLASS
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS # Импортируем для проверки Фантазии
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/ofc_evaluators/ofc_evaluator_5card in mcts_node.py: {e}")
    # Заглушки ... (остаются как были)
    class PlayerBoard: pass
    class Card: pass
    class Deck: FULL_DECK_CARDS = set()
    def get_row_royalty(*args): return 0
    def check_board_foul(*args): return False
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid") # Добавлен класс
    WORST_RANK = 9999
    WORST_CLASS = 9
    ROYALTY_TOP_PAIRS = {}
    RANK_MAP = {}
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    raise ImportError("Missing core logic/evaluator modules for MCTSNode") from e

# Получаем логгер (без изменений)
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)
    # ... (настройка хендлера)

# --- Константы ---
FANTASY_BONUS = 15.0 # Примерный бонус за достижение Фантазии (нуждается в тюнинге)
RANK_QUEEN = RANK_MAP.get('Q', 10) # Получаем числовой ранг Дамы

# --- Воркер для параллельного роллаута ---
# Функция run_parallel_rollout остается почти без изменений,
# так как она уже принимает состояние доски и колоду для симуляции.
# Нужно только убедиться, что она вызывает обновленную static_rollout_simulation.
def run_parallel_rollout(board_dict: dict, remaining_deck_ints: List[int]) -> float:
    """
    Выполняет один роллаут из заданного состояния доски в отдельном процессе.
    Возвращает итоговое роялти (с учетом бонуса за Фантазию).
    Убран параметр cards_to_place_ints, так как он не нужен для роллаута из готового состояния.
    """
    try:
        # Пересоздаем зависимости внутри воркера
        board = PlayerBoard()
        # Восстанавливаем состояние доски из словаря
        board.rows = {r: Card.hand_to_int(cards) for r, cards in board_dict.get('rows', {}).items()}
        board._cards_placed = board_dict.get('_cards_placed', 0)
        # board.is_foul не нужен для начала роллаута, он будет определен в конце

        remaining_deck = set(remaining_deck_ints)

        # Выполняем симуляцию
        final_royalty = MCTSNode.static_rollout_simulation(board, remaining_deck) # Передаем только доску и колоду
        return final_royalty
    except Exception as e:
        print(f"[Worker Error] Error in parallel rollout: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 0.0

# --- Класс MCTSNode ---
class MCTSNode:
    """
    Узел в дереве поиска Монте-Карло (MCTS) для размещения НАБОРА карт OFC.
    """

    def __init__(self,
                 board: PlayerBoard, # Состояние доски *до* размещения текущего набора
                 # cards_to_place: List[int], # Убрано, т.к. размещение происходит при expand
                 remaining_deck: Set[int], # Колода *после* раздачи карт для этого узла
                 parent: Optional['MCTSNode'] = None,
                 # action: Optional[Tuple[int, str, int]] = None): # Убрано, заменено на placement_info
                 placement_info: Optional[Dict[str, Any]] = None): # Информация о том, как сюда попали
        """
        Инициализирует узел MCTS.

        Args:
            board (PlayerBoard): Состояние доски в этом узле.
            remaining_deck (Set[int]): Карты, оставшиеся в колоде для симуляций из этого узла.
            parent (Optional['MCTSNode']): Родительский узел.
            placement_info (Optional[Dict[str, Any]]): Информация о размещении, которое привело
                                                       к этому узлу от родителя.
                                                       Пример: {'placements': [(card, row, idx), ...], 'discarded': card_or_none}
        """
        self.board: PlayerBoard = board # Состояние доски в этом узле
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info # Как мы сюда попали

        self.children: Dict[Tuple[Tuple[int, str, int], ...], 'MCTSNode'] = {} # Ключ - кортеж размещений (hashable)
        # Список неиспробованных *следующих* состояний (доска, сброс), генерируемых для *следующей* улицы
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Optional[int]]]] = None

        self.visits: int = 0
        self.total_reward: float = 0.0 # Суммарное роялти из симуляций

    def is_terminal(self) -> bool:
        """Проверяет, является ли узел терминальным (все 13 карт размещены)."""
        return self.board.is_complete()

    def _generate_next_states(self, cards_dealt_for_next_street: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        """
        Генерирует все возможные следующие состояния доски (и сброшенную карту)
        путем размещения карт, РАЗДАННЫХ для СЛЕДУЮЩЕЙ улицы.
        """
        possible_states = []
        if self.is_terminal() or not cards_dealt_for_next_street:
            return []

        num_to_place: int
        num_to_discard: int

        # Определяем, сколько карт ставить/сбрасывать
        if self.board.get_total_cards() == 0: # Первая улица (5 карт)
            num_to_place = 5
            num_to_discard = 0
            if len(cards_dealt_for_next_street) != 5:
                 logger.error(f"Generate states: Expected 5 cards for street 1, got {len(cards_dealt_for_next_street)}")
                 return []
        else: # Последующие улицы (2 из 3)
            num_to_place = 2
            num_to_discard = 1
            if len(cards_dealt_for_next_street) != 3:
                 logger.error(f"Generate states: Expected 3 cards for streets 2-5, got {len(cards_dealt_for_next_street)}")
                 return []

        available_slots = self.board.get_available_slots()
        if len(available_slots) < num_to_place:
            logger.warning(f"Generate states: Not enough slots ({len(available_slots)}) to place {num_to_place} cards.")
            return [] # Невозможно разместить

        # --- Генерация комбинаций ---
        # 1. Выбираем карты для размещения и сброса (если нужно)
        card_combinations_to_place: Generator[Tuple[int, ...], None, None]
        discarded_card: Optional[int] = None

        if num_to_discard == 0: # Улица 1
            card_combinations_to_place = (tuple(cards_dealt_for_next_street),) # Только один набор
            discarded_card = None
        else: # Улицы 2-5
            # Генерируем комбинации 2 карт для размещения, третья идет в сброс
            def gen_place_discard_combos():
                for combo in combinations(cards_dealt_for_next_street, num_to_place):
                    discard = (set(cards_dealt_for_next_street) - set(combo)).pop()
                    yield tuple(combo), discard
            combo_generator = gen_place_discard_combos()

        # 2. Для каждой комбинации карт генерируем размещения по слотам
        for cards_to_place_tuple, current_discarded_card in (combo_generator if num_to_discard > 0 else [(next(card_combinations_to_place), None)]):
            # Генерируем все перестановки размещения выбранных карт по доступным слотам
            for slot_combination in combinations(available_slots, num_to_place):
                # Для каждой комбинации слотов генерируем все перестановки карт по этим слотам
                for card_permutation in permutations(cards_to_place_tuple):
                    try:
                        next_board = self.board.copy()
                        valid_placement = True
                        placements_made: List[Tuple[int, str, int]] = [] # Для ключа словаря

                        for i in range(num_to_place):
                            card = card_permutation[i]
                            row, idx = slot_combination[i]
                            if not next_board.add_card(card, row, idx):
                                valid_placement = False
                                logger.warning(f"Generate states: Failed to add card {Card.to_str(card)} to {row}[{idx}] during permutation.")
                                break # Ошибка в этой перестановке
                            placements_made.append((card, row, idx))

                        if valid_placement:
                            # Создаем ключ для словаря children (кортеж кортежей)
                            placement_key = tuple(sorted(placements_made)) # Сортируем для каноничности
                            # Сохраняем информацию о размещении для дочернего узла
                            placement_info = {'placements': placements_made, 'discarded': current_discarded_card}
                            # Добавляем в результат для expand
                            possible_states.append((next_board, current_discarded_card, placement_key, placement_info))

                    except Exception as e_perm:
                         logger.error(f"Error generating placement permutation: {e_perm}", exc_info=True)

        # Возвращаем уникальные состояния (доска, сброс) и информацию для создания узла
        # Используем dict для уникальности по ключу размещения
        unique_states_dict: Dict[Tuple[Tuple[int, str, int], ...], Tuple[PlayerBoard, Optional[int], Dict[str, Any]]] = {}
        for board, discard, key, info in possible_states:
            if key not in unique_states_dict:
                 unique_states_dict[key] = (board, discard, info)

        # Возвращаем только (board, discard) для untried_next_states
        # и сохраняем полные данные для expand
        self._generated_states_for_expand = unique_states_dict # Сохраняем для expand
        return [(board, discard) for board, discard, _ in unique_states_dict.values()]


    def expand(self) -> Optional['MCTSNode']:
        """
        Расширяет узел, выбирая одно неиспробованное СЛЕДУЮЩЕЕ СОСТОЯНИЕ,
        и создавая для него дочерний узел.
        Требует, чтобы _generate_next_states был вызван заранее (обычно в фазе выбора).
        """
        if self.is_terminal():
            logger.debug("Expand called on terminal node.")
            return None

        if self.untried_next_states is None:
             logger.error("Expand called before _generate_next_states (untried_next_states is None).")
             # Попытка сгенерировать здесь (требует передачи карт следующей улицы, что невозможно)
             # Лучше просто вернуть None
             return None

        if not self.untried_next_states:
            # logger.debug("No untried next states left to expand.")
            return None

        # Выбираем следующее состояние для расширения
        # (board_state, discarded_card) = self.untried_next_states.pop() # Берем из списка
        # Вместо этого, ищем соответствующий ключ в _generated_states_for_expand

        # --- Логика выбора ключа для расширения ---
        # Нам нужен ключ (кортеж размещений), чтобы создать узел
        # Ищем ключ, соответствующий одному из оставшихся untried_next_states

        # Получаем один из оставшихся ключей из сохраненных данных
        found_key = None
        state_to_expand = None
        if self._generated_states_for_expand:
             # Берем первый попавшийся ключ из словаря
             found_key = next(iter(self._generated_states_for_expand))
             board_state, discarded_card, placement_info = self._generated_states_for_expand.pop(found_key)
             state_to_expand = (board_state, discarded_card) # Нашли состояние для untried_next_states

             # Удаляем соответствующее состояние из untried_next_states
             if state_to_expand in self.untried_next_states:
                 self.untried_next_states.remove(state_to_expand)
             else:
                  logger.warning("State mismatch between _generated_states_for_expand and untried_next_states.")
                  # Пытаемся найти по board hash или другому признаку, если возможно
                  # Или просто пропускаем эту итерацию expand
                  return self.expand() # Пробуем следующее

        if found_key is None or state_to_expand is None:
             logger.error("Could not find a valid state key to expand.")
             return None

        # Создаем дочерний узел
        try:
            child_node = MCTSNode(
                board=state_to_expand[0], # Новое состояние доски
                remaining_deck=self.remaining_deck, # Колода та же, карты были извне
                parent=self,
                placement_info=placement_info # Информация о том, как сюда попали
            )
            self.children[found_key] = child_node # Используем ключ размещения
            # logger.debug(f"Expanded node with key: {found_key}")
            return child_node

        except Exception as e:
            logger.error(f"Error during node expansion for state key {found_key}: {e}", exc_info=True)
            return self.expand() # Пробуем следующее действие при ошибке


    @staticmethod
    def static_rollout_simulation(
            initial_board: PlayerBoard,
            initial_remaining_deck: Set[int]) -> float:
        """
        Статический метод для выполнения симуляции (rollout) из текущего состояния доски.
        Достраивает доску до 13 карт СЛУЧАЙНЫМИ размещениями и возвращает итоговое роялти.
        """
        try:
            current_board = initial_board.copy()
            deck_sim_list = list(initial_remaining_deck) # Преобразуем в список для sample/pop
            random.shuffle(deck_sim_list) # Перемешиваем один раз

            while not current_board.is_complete():
                num_cards_on_board = current_board.get_total_cards()
                num_to_deal: int
                num_to_place: int

                if num_cards_on_board == 0: # Улица 1 (не должно быть в роллауте, но на всякий случай)
                    num_to_deal = 5
                    num_to_place = 5
                else: # Улицы 2-5
                    num_to_deal = 3
                    num_to_place = 2

                # Проверяем, достаточно ли карт в колоде
                if len(deck_sim_list) < num_to_deal:
                    logger.warning(f"Rollout: Not enough cards in deck ({len(deck_sim_list)}) to deal {num_to_deal}.")
                    return 0.0 # Не можем продолжить симуляцию

                # Раздаем карты
                dealt_cards = [deck_sim_list.pop() for _ in range(num_to_deal)]

                # Выбираем карты для размещения (случайно)
                cards_to_place: List[int]
                if num_to_place < num_to_deal:
                    cards_to_place = random.sample(dealt_cards, num_to_place)
                else:
                    cards_to_place = dealt_cards

                # Получаем доступные слоты
                available_slots = current_board.get_available_slots()
                if len(available_slots) < num_to_place:
                    logger.warning(f"Rollout: Not enough slots ({len(available_slots)}) to place {num_to_place} cards.")
                    return 0.0 # Не можем разместить

                # Выбираем слоты для размещения (случайно)
                slots_to_use = random.sample(available_slots, num_to_place)

                # Размещаем карты
                for i in range(num_to_place):
                    card = cards_to_place[i]
                    row, idx = slots_to_use[i]
                    if not current_board.add_card(card, row, idx):
                        logger.warning(f"Rollout: Failed to place card {Card.to_str(card)} in slot {row}[{idx}] during random placement.")
                        # Это не должно происходить при случайном размещении в пустые слоты
                        return 0.0 # Считаем ошибкой

            # Доска заполнена, считаем роялти
            if not current_board.is_complete():
                logger.warning(f"Rollout finished unexpectedly with incomplete board ({current_board.get_total_cards()}/13).")
                return 0.0

            # Проверяем фол
            is_foul = check_board_foul(current_board)
            if is_foul:
                return 0.0 # Роялти за фол = 0

            # Считаем стандартное роялти
            total_royalty = 0.0
            for row_name in PlayerBoard.ROW_NAMES:
                row_cards = current_board.get_row_cards(row_name)
                total_royalty += get_row_royalty(row_cards, row_name)

            # --- Добавляем бонус за Фантазию ---
            top_row_cards = current_board.get_row_cards("top")
            if len(top_row_cards) == 3:
                 rank_t, class_t, type_t = get_hand_rank_safe(top_row_cards)
                 if rank_t != WORST_RANK:
                     is_fantasy_hand = False
                     if class_t == 6: # Trips
                         is_fantasy_hand = True
                     elif class_t == 8: # Pair
                         ranks = [Card.get_rank_int(c) for c in top_row_cards]
                         rank_counts = Counter(ranks)
                         pair_rank = -1
                         for r, count in rank_counts.items():
                             if count == 2: pair_rank = r; break
                         # Проверяем, что пара QQ или выше
                         if pair_rank >= RANK_QUEEN:
                             is_fantasy_hand = True

                     if is_fantasy_hand:
                         logger.debug(f"Rollout: Fantasy condition met (Top: {type_t}). Adding bonus {FANTASY_BONUS}.")
                         total_royalty += FANTASY_BONUS

            return total_royalty

        except Exception as e:
            logger.error(f"Error during static rollout simulation: {e}", exc_info=True)
            return 0.0

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        """Выбирает дочерний узел с использованием формулы UCB1."""
        best_score = -float('inf')
        best_child = None

        parent_visits_log = math.log(self.visits + 1)
        children_items = list(self.children.items()) # Используем .items() для доступа к узлам
        if not children_items: return None

        random.shuffle(children_items) # Для случайного выбора при равных очках

        for placement_key, child in children_items: # Итерируем по ключу и узлу
            if child.visits == 0:
                # Даем очень высокий приоритет неисследованным узлам,
                # но не бесконечность, чтобы избежать проблем с float
                score = 1e6 + random.random() # Добавляем немного случайности
            else:
                exploit_term = child.total_reward / child.visits
                explore_term = exploration_constant * math.sqrt(parent_visits_log / child.visits)
                score = exploit_term + explore_term

            if score > best_score:
                best_score = score
                best_child = child
            # Убрал случайный выбор при равных, т.к. shuffle в начале уже есть

        # Если все потомки имеют score = -inf (маловероятно), выбираем случайно
        if best_child is None and children_items:
             logger.warning("UCT selection resulted in None, choosing random child.")
             best_child = random.choice([c for _, c in children_items])

        return best_child


    def backpropagate(self, reward: float):
        """Обновляет статистику узлов вдоль пути."""
        node = self
        while node is not None:
            node.visits += 1
            node.total_reward += reward
            node = node.parent

    def __repr__(self):
        """Строковое представление узла для отладки."""
        q_val = self.total_reward / self.visits if self.visits > 0 else 0.0
        action_str = "Root"
        if self.placement_info and self.placement_info.get('placements'):
             # Форматируем первое размещение для представления
             p = self.placement_info['placements'][0]
             action_str = f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]..."
             if self.placement_info.get('discarded'):
                 action_str += f" (Discard: {Card.to_str(self.placement_info['discarded'])})"

        return (f"[Node V={self.visits} R={q_val:.2f} "
                f"NChild={len(self.children)} UStates={len(self.untried_next_states or [])} "
                f"Act={action_str}]")
