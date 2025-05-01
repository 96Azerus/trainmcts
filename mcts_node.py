# mcts_node.py v2.2 (Refactored for Set Placement, Iterator Fix, Fantasy Bonus Increased)
"""
Представление узла дерева MCTS для задачи размещения НАБОРА карт OFC Pineapple.
Цель - максимизация роялти с учетом цели "Фантазия".
Использует UCT/UCB1. Параллельные роллауты.
Исправлена ошибка итератора при генерации состояний для 5 карт.
Увеличен бонус за Фантазию.
"""

import math
import time
import random
import multiprocessing
import traceback
import sys
import logging
from typing import Optional, Any, List, Tuple, Set, Dict, Generator
from itertools import combinations, permutations
from collections import Counter

# Импорты из локальных модулей
try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/ofc_evaluators/ofc_evaluator_5card in mcts_node.py: {e}")
    # Заглушки ...
    class PlayerBoard: pass
    class Card: pass
    class Deck: FULL_DECK_CARDS = set()
    def get_row_royalty(*args): return 0
    def check_board_foul(*args): return False
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    WORST_RANK = 9999; WORST_CLASS = 9
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {}
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    raise ImportError("Missing core logic/evaluator modules for MCTSNode") from e

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Константы ---
FANTASY_BONUS = 75.0 # <--- УВЕЛИЧЕНО ЗНАЧЕНИЕ
RANK_QUEEN = RANK_MAP.get('Q', 10)

# --- Воркер для параллельного роллаута ---
def run_parallel_rollout(board_dict: dict, remaining_deck_ints: List[int]) -> float:
    """
    Выполняет один роллаут из заданного состояния доски в отдельном процессе.
    Возвращает итоговое роялти (с учетом бонуса за Фантазию).
    """
    try:
        board = PlayerBoard()
        board.rows = {r: Card.hand_to_int(cards) for r, cards in board_dict.get('rows', {}).items()}
        board._cards_placed = board_dict.get('_cards_placed', 0)
        remaining_deck = set(remaining_deck_ints)
        final_royalty = MCTSNode.static_rollout_simulation(board, remaining_deck)
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
                 board: PlayerBoard,
                 remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 placement_info: Optional[Dict[str, Any]] = None):
        self.board: PlayerBoard = board
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info
        self.children: Dict[Tuple[Tuple[int, str, int], ...], 'MCTSNode'] = {}
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Optional[int]]]] = None
        self._generated_states_for_expand: Dict[Tuple[Tuple[int, str, int], ...], Tuple[PlayerBoard, Optional[int], Dict[str, Any]]] = {}
        self.visits: int = 0
        self.total_reward: float = 0.0

    def is_terminal(self) -> bool:
        return self.board.is_complete()

    def _generate_next_states(self, cards_dealt_for_next_street: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        """
        Генерирует все возможные следующие состояния доски (и сброшенную карту)
        путем размещения карт, РАЗДАННЫХ для СЛЕДУЮЩЕЙ улицы.
        """
        possible_states_data = []
        self._generated_states_for_expand.clear()

        if self.is_terminal() or not cards_dealt_for_next_street:
            return []

        num_to_place: int; num_to_discard: int
        num_dealt = len(cards_dealt_for_next_street)

        if self.board.get_total_cards() == 0:
            num_to_place = 5; num_to_discard = 0
            if num_dealt != 5:
                 logger.error(f"Generate states: Expected 5 cards for street 1, got {num_dealt}")
                 return []
        else:
            num_to_place = 2; num_to_discard = 1
            if num_dealt != 3:
                 logger.error(f"Generate states: Expected 3 cards for streets 2-5, got {num_dealt}")
                 return []

        available_slots = self.board.get_available_slots()
        if len(available_slots) < num_to_place:
            # logger.warning(f"Generate states: Not enough slots ({len(available_slots)}) to place {num_to_place} cards.")
            return []

        combo_iterable: Any
        if num_to_discard == 0:
            cards_to_place_tuple = tuple(cards_dealt_for_next_street)
            combo_iterable = [(cards_to_place_tuple, None)]
        else:
            def gen_place_discard_combos():
                for combo in combinations(cards_dealt_for_next_street, num_to_place):
                    discard_list = [c for c in cards_dealt_for_next_street if c not in combo]
                    discard = discard_list[0] if discard_list else None
                    if discard is None: continue
                    yield tuple(combo), discard
            combo_iterable = gen_place_discard_combos()

        for cards_to_place_tuple, current_discarded_card in combo_iterable:
            for slot_combination in combinations(available_slots, num_to_place):
                for card_permutation in permutations(cards_to_place_tuple):
                    try:
                        next_board = self.board.copy()
                        valid_placement = True
                        placements_made: List[Tuple[int, str, int]] = []
                        for i in range(num_to_place):
                            card = card_permutation[i]
                            row, idx = slot_combination[i]
                            if not next_board.add_card(card, row, idx):
                                valid_placement = False; break
                            placements_made.append((card, row, idx))

                        if valid_placement:
                            placement_key = tuple(sorted(placements_made))
                            placement_info = {'placements': placements_made, 'discarded': current_discarded_card}
                            if placement_key not in self._generated_states_for_expand:
                                 self._generated_states_for_expand[placement_key] = (next_board, current_discarded_card, placement_info)
                                 possible_states_data.append((next_board, current_discarded_card))
                    except Exception as e_perm:
                         logger.error(f"Error generating placement permutation: {e_perm}", exc_info=True)

        unique_next_states = list({state_tuple: None for state_tuple in possible_states_data}.keys())
        random.shuffle(unique_next_states)
        return unique_next_states

    def expand(self) -> Optional['MCTSNode']:
        """
        Расширяет узел, выбирая одно неиспробованное СЛЕДУЮЩЕЕ СОСТОЯНИЕ,
        и создавая для него дочерний узел.
        """
        if self.is_terminal(): return None
        if self.untried_next_states is None:
             logger.error("Expand called before _generate_next_states (untried_next_states is None).")
             return None
        if not self.untried_next_states: return None

        state_to_expand = self.untried_next_states.pop()
        board_state, discarded_card = state_to_expand

        found_key = None
        placement_info = None
        # Ищем ключ/инфо в копии словаря, чтобы избежать ошибки изменения во время итерации
        items_to_check = list(self._generated_states_for_expand.items())
        for key, (board, discard, info) in items_to_check:
             if board.get_board_state_tuple() == board_state.get_board_state_tuple() and discard == discarded_card:
                 found_key = key
                 placement_info = info
                 # Удаляем найденный элемент из оригинального словаря
                 if key in self._generated_states_for_expand:
                      del self._generated_states_for_expand[key]
                 break

        if found_key is None or placement_info is None:
             logger.error(f"Could not find matching key/info for state to expand: {state_to_expand}")
             return self.expand() if self.untried_next_states else None

        try:
            child_node = MCTSNode(
                board=board_state,
                remaining_deck=self.remaining_deck,
                parent=self,
                placement_info=placement_info
            )
            self.children[found_key] = child_node
            return child_node
        except Exception as e:
            logger.error(f"Error during node expansion for state key {found_key}: {e}", exc_info=True)
            return self.expand() if self.untried_next_states else None

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
            deck_sim_list = list(initial_remaining_deck)
            random.shuffle(deck_sim_list)

            while not current_board.is_complete():
                num_cards_on_board = current_board.get_total_cards()
                num_to_deal: int; num_to_place: int
                if num_cards_on_board == 0: num_to_deal = 5; num_to_place = 5
                else: num_to_deal = 3; num_to_place = 2

                if len(deck_sim_list) < num_to_deal: return 0.0
                dealt_cards = [deck_sim_list.pop() for _ in range(num_to_deal)]

                cards_to_place: List[int]
                if num_to_place < num_to_deal: cards_to_place = random.sample(dealt_cards, num_to_place)
                else: cards_to_place = dealt_cards

                available_slots = current_board.get_available_slots()
                if len(available_slots) < num_to_place: return 0.0
                slots_to_use = random.sample(available_slots, num_to_place)

                for i in range(num_to_place):
                    card = cards_to_place[i]; row, idx = slots_to_use[i]
                    if not current_board.add_card(card, row, idx):
                        logger.error(f"Rollout Error: Failed to place card {Card.to_str(card)} in slot {row}[{idx}] during random placement.")
                        return 0.0

            is_foul = check_board_foul(current_board)
            if is_foul: return 0.0

            total_royalty = 0.0
            for row_name in PlayerBoard.ROW_NAMES:
                row_cards = current_board.get_row_cards(row_name)
                total_royalty += get_row_royalty(row_cards, row_name)

            top_row_cards = current_board.get_row_cards("top")
            if len(top_row_cards) == 3:
                 rank_t, class_t, type_t = get_hand_rank_safe(top_row_cards)
                 if rank_t != WORST_RANK:
                     is_fantasy_hand = False
                     if class_t == 6: is_fantasy_hand = True
                     elif class_t == 8:
                         ranks = [Card.get_rank_int(c) for c in top_row_cards]
                         rank_counts = Counter(ranks)
                         pair_rank = -1
                         for r, count in rank_counts.items():
                             if count == 2: pair_rank = r; break
                         if pair_rank >= RANK_QUEEN: is_fantasy_hand = True
                     if is_fantasy_hand: total_royalty += FANTASY_BONUS
            return total_royalty
        except Exception as e:
            logger.error(f"Error during static rollout simulation: {e}", exc_info=True)
            return 0.0

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        """Выбирает дочерний узел с использованием формулы UCB1."""
        best_score = -float('inf'); best_child = None
        parent_visits_log = math.log(self.visits + 1)
        children_items = list(self.children.items())
        if not children_items: return None
        random.shuffle(children_items)

        for placement_key, child in children_items:
            if child.visits == 0: score = 1e6 + random.random()
            else:
                exploit_term = child.total_reward / child.visits
                explore_term = exploration_constant * math.sqrt(parent_visits_log / child.visits)
                score = exploit_term + explore_term
            if score > best_score: best_score = score; best_child = child

        if best_child is None and children_items:
             logger.warning("UCT selection resulted in None, choosing random child.")
             best_child = random.choice([c for _, c in children_items])
        return best_child

    def backpropagate(self, reward: float):
        """Обновляет статистику узлов вдоль пути."""
        node = self
        while node is not None:
            node.visits += 1; node.total_reward += reward
            node = node.parent

    def __repr__(self):
        """Строковое представление узла для отладки."""
        q_val = self.total_reward / self.visits if self.visits > 0 else 0.0
        action_str = "Root"
        if self.placement_info and self.placement_info.get('placements'):
             p_list = self.placement_info['placements']
             action_str = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_list])
             if self.placement_info.get('discarded'):
                 action_str += f" (D: {Card.to_str(self.placement_info['discarded'])})"
        return (f"[Node V={self.visits} R={q_val:.2f} "
                f"NChild={len(self.children)} UStates={len(self.untried_next_states or [])} "
                f"Act={action_str}]")
