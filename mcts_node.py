# mcts_node.py v2.4 (Random Rollout + RAVE + PW)
"""
Представление узла дерева MCTS для задачи размещения НАБОРА карт OFC Pineapple.
Использует случайную симуляцию с трекингом действий, RAVE и Progressive Widening (PW).
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
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, MAX_HIGH_CARD_5, RANK_QUEEN
    )
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/ofc_evaluators in mcts_node.py: {e}")
    # Заглушки ...
    class PlayerBoard: pass
    class Card: pass
    class Deck: FULL_DECK_CARDS = set()
    def get_row_royalty(*args): return 0
    def check_board_foul(*args): return False
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    WORST_RANK = 9999; WORST_CLASS = 9; MAX_HIGH_CARD_5 = 7462
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {}; STR_RANKS = ""; RANK_QUEEN = 10
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
FANTASY_BONUS = 25.0
RAVE_K = 500.0
# --- Параметры Progressive Widening (PW) ---
# C * (N+1)^ALPHA >= k
PW_C = 2.0  # Константа C (настроить)
PW_ALPHA = 0.5 # Константа Alpha (0 < alpha < 1) (настроить)

# --- Воркер для параллельного роллаута ---
def run_parallel_rollout(board_dict: dict, remaining_deck_ints: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Выполняет один СЛУЧАЙНЫЙ роллаут из заданного состояния доски.
    Возвращает (итоговое роялти + бонус FL, список сделанных ходов).
    """
    # ... (код функции run_parallel_rollout без изменений) ...
    try:
        board = PlayerBoard()
        board.rows = {r: Card.hand_to_int(cards) for r, cards in board_dict.get('rows', {}).items()}
        board._cards_placed = board_dict.get('_cards_placed', 0)
        remaining_deck = set(remaining_deck_ints)
        final_score, actions_history = MCTSNode.static_rollout_simulation(board, remaining_deck)
        return final_score, actions_history
    except Exception as e:
        print(f"[Worker Error] Error in parallel random rollout: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 0.0, []

# --- Класс MCTSNode ---
class MCTSNode:
    """
    Узел в дереве поиска Монте-Карло (MCTS) для размещения НАБОРА карт OFC.
    Использует случайную симуляцию, RAVE и Progressive Widening (PW).
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
        # RAVE Статистика
        self.rave_visits: int = 0
        self.rave_reward: float = 0.0

    def is_terminal(self) -> bool:
        return self.board.is_complete()

    def _generate_next_states(self, cards_dealt_for_next_street: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        """Генерирует следующие состояния (без изменений)."""
        # ... (код функции _generate_next_states без изменений) ...
        possible_states_data = []
        self._generated_states_for_expand.clear()
        if self.is_terminal() or not cards_dealt_for_next_street: return []
        num_to_place: int; num_to_discard: int
        num_dealt = len(cards_dealt_for_next_street)
        if self.board.get_total_cards() == 0:
            num_to_place = 5; num_to_discard = 0
            if num_dealt != 5: logger.error(f"Generate states: Expected 5 cards for street 1, got {num_dealt}"); return []
        else:
            num_to_place = 2; num_to_discard = 1
            if num_dealt != 3: logger.error(f"Generate states: Expected 3 cards for streets 2-5, got {num_dealt}"); return []
        available_slots = self.board.get_available_slots()
        if len(available_slots) < num_to_place: return []
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
                        next_board = self.board.copy(); valid_placement = True
                        placements_made: List[Tuple[int, str, int]] = []
                        for i in range(num_to_place):
                            card = card_permutation[i]; row, idx = slot_combination[i]
                            if not next_board.add_card(card, row, idx): valid_placement = False; break
                            placements_made.append((card, row, idx))
                        if valid_placement:
                            placement_key = tuple(sorted(placements_made))
                            placement_info = {'placements': placements_made, 'discarded': current_discarded_card}
                            if placement_key not in self._generated_states_for_expand:
                                 self._generated_states_for_expand[placement_key] = (next_board, current_discarded_card, placement_info)
                                 possible_states_data.append((next_board, current_discarded_card))
                    except Exception as e_perm: logger.error(f"Error generating placement permutation: {e_perm}", exc_info=True)
        unique_next_states = list({state_tuple: None for state_tuple in possible_states_data}.keys())
        random.shuffle(unique_next_states)
        return unique_next_states


    def expand(self) -> Optional['MCTSNode']:
        """
        Расширяет узел, создавая дочерний узел для одного неиспробованного состояния,
        ЕСЛИ это разрешено правилом Progressive Widening (PW).
        """
        if self.is_terminal(): return None
        if self.untried_next_states is None: logger.error("Expand called before _generate_next_states"); return None
        if not self.untried_next_states: return None # Нет состояний для расширения

        # --- Проверка Progressive Widening ---
        num_children = len(self.children)
        # Используем self.visits + 1, чтобы разрешить расширение с самого начала
        allowed_children = PW_C * math.pow(self.visits + 1, PW_ALPHA)

        if num_children >= allowed_children:
            # Лимит PW достигнут, не расширяем дальше на этой итерации
            logger.debug(f"PW limit reached for node {self}: children={num_children}, allowed={allowed_children:.2f}")
            return None
        # --- Конец проверки PW ---

        # Если PW позволяет, продолжаем расширение
        state_to_expand = self.untried_next_states.pop()
        board_state, discarded_card = state_to_expand
        board_state_tuple = board_state.get_board_state_tuple()
        found_key = None; placement_info = None
        for key, (board, discard, info) in self._generated_states_for_expand.items():
             if board.get_board_state_tuple() == board_state_tuple and discard == discarded_card:
                 found_key = key; placement_info = info; break

        if found_key is None or placement_info is None:
             logger.error(f"Could not find matching key/info for state to expand: {state_to_expand}")
             # Попробуем следующее состояние, если есть
             return self.expand() if self.untried_next_states else None

        try:
            child_node = MCTSNode(board=board_state, remaining_deck=self.remaining_deck, parent=self, placement_info=placement_info)
            self.children[found_key] = child_node
            logger.debug(f"Expanded node with key: {found_key} (PW allowed: {num_children+1}/{allowed_children:.2f})")
            return child_node
        except Exception as e:
            logger.error(f"Error during node expansion for state key {found_key}: {e}", exc_info=True)
            # Вернем состояние обратно в список? Нет, лучше пропустить его.
            return self.expand() if self.untried_next_states else None


    @staticmethod
    def static_rollout_simulation(
            initial_board: PlayerBoard,
            initial_remaining_deck: Set[int]) -> Tuple[float, List[Dict[str, Any]]]:
        """
        Статический метод для выполнения СЛУЧАЙНОЙ симуляции (rollout).
        Возвращает (итоговый счет, список сделанных ходов).
        """
        # ... (код функции static_rollout_simulation без изменений) ...
        actions_history: List[Dict[str, Any]] = []
        try:
            current_board = initial_board.copy()
            deck_sim_list = list(initial_remaining_deck); random.shuffle(deck_sim_list)
            while not current_board.is_complete():
                num_cards_on_board = current_board.get_total_cards()
                num_to_deal = 3 if num_cards_on_board > 0 else 5
                num_to_place = 2 if num_cards_on_board > 0 else 5
                if len(deck_sim_list) < num_to_deal: return 0.0, actions_history
                dealt_cards = [deck_sim_list.pop() for _ in range(num_to_deal)]
                cards_to_place: List[int]; discarded_card: Optional[int] = None
                if num_to_place < num_to_deal:
                    cards_to_place = random.sample(dealt_cards, num_to_place)
                    discarded_list = [c for c in dealt_cards if c not in cards_to_place]
                    discarded_card = discarded_list[0] if discarded_list else None
                else: cards_to_place = dealt_cards
                available_slots = current_board.get_available_slots()
                if len(available_slots) < num_to_place: return 0.0, actions_history
                slots_to_use = random.sample(available_slots, num_to_place)
                placements: List[Tuple[int, str, int]] = []; valid_placement = True
                for i in range(num_to_place):
                    card = cards_to_place[i]; row, idx = slots_to_use[i]
                    if not current_board.add_card(card, row, idx): valid_placement = False; break
                    placements.append((card, row, idx))
                if not valid_placement: return 0.0, actions_history
                action = {'placements': placements, 'discarded': discarded_card}
                actions_history.append(action)
            is_foul = check_board_foul(current_board)
            if is_foul: return 0.0, actions_history
            total_royalty = 0.0
            for row_name in PlayerBoard.ROW_NAMES:
                row_cards = current_board.get_row_cards(row_name)
                total_royalty += get_row_royalty(row_cards, row_name)
            final_fantasy_bonus = 0.0
            top_row_cards = current_board.get_row_cards("top")
            if len(top_row_cards) == 3:
                 rank_t, class_t, type_t = get_hand_rank_safe(top_row_cards)
                 if rank_t != WORST_RANK:
                     is_fantasy_hand = False
                     if class_t == 6: is_fantasy_hand = True
                     elif class_t == 8:
                         ranks = [Card.get_rank_int(c) for c in top_row_cards]
                         pair_rank = next((r for r, count in Counter(ranks).items() if count == 2), -1)
                         if pair_rank >= RANK_QUEEN: is_fantasy_hand = True
                     if is_fantasy_hand: final_fantasy_bonus = FANTASY_BONUS
            final_score = total_royalty + final_fantasy_bonus
            return final_score, actions_history
        except Exception as e:
            logger.error(f"Error during static rollout simulation: {e}", exc_info=True)
            return 0.0, actions_history


    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        """Выбирает дочерний узел с использованием формулы UCB1 + RAVE."""
        # ... (код функции uct_select_child без изменений) ...
        best_score = -float('inf'); best_child = None
        parent_visits = self.visits
        if parent_visits == 0: return random.choice(list(self.children.values())) if self.children else None
        parent_visits_log = math.log(parent_visits)
        items = list(self.children.items()); random.shuffle(items)
        beta = math.sqrt(RAVE_K / (3 * parent_visits + RAVE_K))
        for placement_key, child in items:
            child_visits = child.visits
            if child_visits == 0:
                if child.rave_visits > 0:
                    rave_score = child.rave_reward / child.rave_visits
                    score = 1e6 + beta * rave_score + random.random()
                else: score = 1e6 + 10 + random.random()
            else:
                node_score = child.total_reward / child_visits
                rave_score = child.rave_reward / child.rave_visits if child.rave_visits > 0 else node_score
                combined_score = (1.0 - beta) * node_score + beta * rave_score
                explore_term = exploration_constant * math.sqrt(parent_visits_log / child_visits)
                score = combined_score + explore_term
            if score > best_score: best_score = score; best_child = child
        if best_child is None and items:
             logger.warning(f"UCT selection resulted in None for node {self}. Choosing random child.")
             best_child = random.choice([c for _, c in items])
        return best_child


    def backpropagate(self, reward: float):
        """Обновляет стандартную статистику узлов вдоль пути."""
        # ... (код функции backpropagate без изменений) ...
        node = self
        while node is not None:
            node.visits += 1; node.total_reward += reward; node = node.parent

    def backpropagate_rave(self, simulation_actions: List[Dict[str, Any]], reward: float):
        """Обновляет RAVE статистику узлов вдоль пути (AMAF)."""
        # ... (код функции backpropagate_rave без изменений) ...
        sim_action_keys = set()
        for action in simulation_actions:
            placements = action.get('placements')
            if placements:
                 try:
                     action_key = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placements]))
                     sim_action_keys.add(action_key)
                 except Exception as e: logger.warning(f"RAVE Backprop Key Error: {e}")
        if not sim_action_keys: return
        node = self
        while node is not None:
            for child_key, child_node in node.children.items():
                if child_key in sim_action_keys:
                    child_node.rave_visits += 1; child_node.rave_reward += reward
            node = node.parent

    def __repr__(self):
        """Строковое представление узла для отладки."""
        # ... (код функции __repr__ без изменений) ...
        q_val = self.total_reward / self.visits if self.visits > 0 else 0.0
        rave_q_val = self.rave_reward / self.rave_visits if self.rave_visits > 0 else 0.0
        action_str = "Root"
        if self.placement_info and self.placement_info.get('placements'):
             p_list = self.placement_info['placements']
             action_str = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_list])
             if self.placement_info.get('discarded'): action_str += f" (D: {Card.to_str(self.placement_info['discarded'])})"
        return (f"[Node V={self.visits} R={q_val:.2f} RV={self.rave_visits} RR={rave_q_val:.2f} "
                f"NChild={len(self.children)} UStates={len(self.untried_next_states or [])} "
                f"Act={action_str}]")
