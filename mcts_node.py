# mcts_node.py v2.8.1 (Syntax Fix in Stubs, Enhanced Evaluation Logic)
# ИСПРАВЛЕНО: Синтаксическая ошибка в заглушках PlayerBoard.
# ИЗМЕНЕНО: Улучшенная оценка поддержки Фантазии для мидла.
# ИЗМЕНЕНО: Улучшенная оценка боттома (штраф за пустой низ, бонус за пару на боттоме).
# ИЗМЕНЕНО: "Экспертное" правило для сильных рук на первой улице на боттоме.
# ИЗМЕНЕНО: Корректировка некоторых весов в _estimate_row_potential.
# (Включает все предыдущие исправления: TypeError, get_hand_rank_safe, учет сброса, бонусы Фантазии, полное колесо)
"""
Представление узла дерева MCTS для задачи размещения НАБОРА карт OFC Pineapple.
Использует ПРОДВИНУТУЮ ЭВРИСТИЧЕСКУЮ симуляцию v2.8.1:
- Улучшено распознавание стрит-дро (колесо, гэпы).
- Добавлена продвинутая проверка безопасности Fantasyland.
- Улучшена оценка поддержки Фантазии, построения снизу-вверх.
- Использует RAVE и PW.
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
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS, INT_RANKS, PRIMES
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, MAX_HIGH_CARD_5,
        RANK_QUEEN, RANK_KING, RANK_ACE,
        evaluate_3_card_ofc, HAND_TYPE_TRIPS_3, HAND_TYPE_PAIR_3,
        evaluator_5card
    )
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/ofc_evaluators in mcts_node.py: {e}")
    # Заглушки для возможности анализа без ofc_logic/ofc_evaluators
    class PlayerBoard: # type: ignore
        ROW_NAMES = ['top', 'middle', 'bottom']; ROW_CAPACITY = {'top': 3, 'middle': 5, 'bottom': 5}
        TOTAL_CAPACITY = 13
        def __init__(self): self.rows = {r:[] for r in self.ROW_NAMES}; self._cards_placed = 0; self.is_foul = False
        def add_card(self, c, r, i): return False
        def get_row_cards(self, rn): return []
        def get_all_cards(self): return set()
        def get_available_slots(self): return []
        def get_total_cards(self): return 0
        def is_complete(self): return False
        def get_board_state_tuple(self): return tuple()
        def copy(self): return PlayerBoard()
    class Card: # type: ignore
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
    class Deck: FULL_DECK_CARDS = set() # type: ignore
    def get_row_royalty(*args): return 0 # type: ignore
    def check_board_foul(*args): return False # type: ignore
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid") # type: ignore
    def evaluate_3_card_ofc(*args): return (999, "Error", "ERR") # type: ignore
    class MockEvaluator5Card: evaluate = lambda s, c: 9999 # type: ignore
    evaluator_5card = MockEvaluator5Card() # type: ignore
    WORST_RANK = 9999; WORST_CLASS = 9; MAX_HIGH_CARD_5 = 7462
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {'A':12, 'K':11, 'Q':10, 'J':9, 'T':8, '9':7, '8':6, '7':5, '6':4, '5':3, '4':2, '3':1, '2':0}
    STR_RANKS = ""; RANK_QUEEN = 10; RANK_KING = 11; RANK_ACE = 12; HAND_TYPE_TRIPS_3 = ""; HAND_TYPE_PAIR_3 = ""
    INT_RANKS = range(13); PRIMES = [] # type: ignore
    raise ImportError("Missing core logic/evaluator modules for MCTSNode") from e


logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Константы ---
FANTASY_BONUS = 70.0
RAVE_K = 500.0
PW_C = 2.0
PW_ALPHA = 0.5

HEURISTIC_FOUL_PENALTY = -10000.0
HEURISTIC_FL_QUALIFY_BONUS = 25.0
HEURISTIC_FL_REPEAT_BONUS = 10.0
HEURISTIC_FL_RISK_PENALTY_FACTOR = -2.0

FIRST_STREET_STRONG_HAND_ON_BOTTOM_BONUS = 50.0
BOTTOM_ROW_PAIR_BONUS = 2.0
EMPTY_BOTTOM_PENALTY = -5.0
FANTASY_SUPPORT_OUT_WEIGHT = 0.7

ROW_COMPLETED_HAND_WEIGHT = 1.0
ROW_FLUSH_DRAW_OUT_WEIGHT = 0.8
ROW_STRAIGHT_DRAW_OUT_WEIGHT = 0.6
ROW_GUTSHOT_DRAW_OUT_WEIGHT = 0.3
ROW_PAIR_OUTS_WEIGHT = 0.5
ROW_TRIPS_OUTS_WEIGHT = 0.6
ROW_HIGH_CARD_WEIGHT = 0.005

def run_parallel_rollout(board_dict: dict, remaining_deck_ints: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    try:
        board = PlayerBoard()
        board.rows = {r: Card.hand_to_int(cards) for r, cards in board_dict.get('rows', {}).items()}
        board._cards_placed = board_dict.get('_cards_placed', 0)
        remaining_deck = set(remaining_deck_ints)
        final_score, actions_history = MCTSNode.heuristic_rollout_simulation_v2(board, remaining_deck)
        return final_score, actions_history
    except Exception as e:
        print(f"[Worker Error] Error in parallel advanced heuristic rollout: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 0.0, []

class MCTSNode:
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
        self.rave_visits: int = 0
        self.rave_reward: float = 0.0

    def is_terminal(self) -> bool:
        return self.board.is_complete()

    def _generate_next_states(self, cards_dealt_for_next_street: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        possible_states_data = []
        self._generated_states_for_expand.clear()

        if self.is_terminal() or not cards_dealt_for_next_street:
            return []

        num_dealt = len(cards_dealt_for_next_street)
        num_on_board = self.board.get_total_cards()

        if num_on_board == 0:
            num_to_place = 5; num_to_discard = 0
            if num_dealt != 5: logger.error(f"Generate states: Expected 5 cards for street 1, got {num_dealt}"); return []
        else:
            num_to_place = 2; num_to_discard = 1
            if num_dealt != 3: logger.error(f"Generate states: Expected 3 cards for streets 2-5, got {num_dealt}"); return []

        available_slots = self.board.get_available_slots()
        if len(available_slots) < num_to_place:
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
        if self.is_terminal(): return None
        if self.untried_next_states is None:
            logger.error("Expand called before _generate_next_states was called or after it was exhausted.")
            return None
        if not self.untried_next_states:
            return None

        num_children = len(self.children)
        allowed_children = PW_C * math.pow(self.visits + 1, PW_ALPHA)
        if num_children >= allowed_children:
            return None

        state_to_expand = self.untried_next_states.pop()
        board_state, discarded_card_from_generation = state_to_expand
        board_state_tuple = board_state.get_board_state_tuple()

        found_key = None
        placement_info_for_child = None
        for key, (board_gen, discard_gen, info_gen) in self._generated_states_for_expand.items():
             if board_gen.get_board_state_tuple() == board_state_tuple and discard_gen == discarded_card_from_generation:
                 found_key = key
                 placement_info_for_child = info_gen
                 break
        
        if found_key is None or placement_info_for_child is None:
             logger.error(f"Could not find matching key/info for state to expand: {state_to_expand}")
             return self.expand() if self.untried_next_states else None

        try:
            child_deck = self.remaining_deck.copy()
            if placement_info_for_child and placement_info_for_child.get('placements'):
                for p_card, _, _ in placement_info_for_child['placements']:
                    if p_card in child_deck:
                        child_deck.remove(p_card)
            if discarded_card_from_generation is not None:
                if discarded_card_from_generation in child_deck:
                    child_deck.remove(discarded_card_from_generation)
            
            child_node = MCTSNode(
                board=board_state,
                remaining_deck=child_deck, 
                parent=self,
                placement_info=placement_info_for_child
            )
            self.children[found_key] = child_node
            return child_node
        except Exception as e:
            logger.error(f"Error during node expansion for state key {found_key}: {e}", exc_info=True)
            return self.expand() if self.untried_next_states else None

    @staticmethod
    def _count_outs(needed_cards: Set[int], remaining_deck: Set[int]) -> int:
        return len(needed_cards.intersection(remaining_deck))

    @staticmethod
    def _detect_flush_draw(cards: List[int]) -> Tuple[Optional[int], int]:
        if not cards: return None, 0
        suits = Counter(Card.get_suit_int(c) for c in cards)
        for suit, count in suits.items():
            if count >= 3: return suit, count
        return None, 0

    @staticmethod
    def _get_flush_draw_outs(target_suit: int, board_cards: Set[int], remaining_deck: Set[int]) -> Set[int]:
        outs = set()
        for card in remaining_deck:
            if Card.get_suit_int(card) == target_suit and card not in board_cards:
                outs.add(card)
        return outs

    @staticmethod
    def _detect_straight_draw(cards: List[int]) -> Tuple[int, Set[int]]:
        if len(cards) < 3: return 0, set()
        
        ranks_int = sorted(list(set(Card.get_rank_int(c) for c in cards)))
        rank_set = set(ranks_int)
        needed_ranks = set()
        draw_type = 0 

        ACE_R = RANK_ACE # Используем импортированные константы
        TWO_R = RANK_MAP['2']
        THREE_R = RANK_MAP['3']
        FOUR_R = RANK_MAP['4']
        FIVE_R = RANK_MAP['5']
        SIX_R = RANK_MAP['6']

        wheel_component_ranks = {ACE_R, TWO_R, THREE_R, FOUR_R, FIVE_R}
        present_wheel_ranks = rank_set.intersection(wheel_component_ranks)

        if len(present_wheel_ranks) >= 3:
            if {ACE_R, TWO_R, THREE_R, FOUR_R}.issubset(present_wheel_ranks): needed_ranks.add(FIVE_R); draw_type = max(draw_type, 2)
            elif {ACE_R, TWO_R, THREE_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(FOUR_R); draw_type = max(draw_type, 1)
            elif {ACE_R, TWO_R, FOUR_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(THREE_R); draw_type = max(draw_type, 1)
            elif {ACE_R, THREE_R, FOUR_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(TWO_R); draw_type = max(draw_type, 1)
            elif {TWO_R, THREE_R, FOUR_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(ACE_R); draw_type = max(draw_type, 2)
            elif len(present_wheel_ranks) == 3:
                 if {ACE_R, TWO_R, THREE_R}.issubset(present_wheel_ranks): needed_ranks.add(FOUR_R); needed_ranks.add(FIVE_R); draw_type = max(draw_type, 1)
                 elif {ACE_R, TWO_R, FOUR_R}.issubset(present_wheel_ranks): needed_ranks.add(THREE_R); needed_ranks.add(FIVE_R); draw_type = max(draw_type, 1)
                 elif {ACE_R, TWO_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(THREE_R); needed_ranks.add(FOUR_R); draw_type = max(draw_type, 1)
                 elif {ACE_R, THREE_R, FOUR_R}.issubset(present_wheel_ranks): needed_ranks.add(TWO_R); needed_ranks.add(FIVE_R); draw_type = max(draw_type, 1)
                 elif {ACE_R, THREE_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(TWO_R); needed_ranks.add(FOUR_R); draw_type = max(draw_type, 1)
                 elif {ACE_R, FOUR_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(TWO_R); needed_ranks.add(THREE_R); draw_type = max(draw_type, 1)
                 elif {TWO_R, THREE_R, FOUR_R}.issubset(present_wheel_ranks): needed_ranks.add(ACE_R); needed_ranks.add(FIVE_R); draw_type = max(draw_type, 1)
                 elif {TWO_R, THREE_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(FOUR_R); draw_type = max(draw_type, 1)
                 elif {TWO_R, FOUR_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(THREE_R); draw_type = max(draw_type, 1)
                 elif {THREE_R, FOUR_R, FIVE_R}.issubset(present_wheel_ranks): needed_ranks.add(TWO_R); needed_ranks.add(SIX_R); draw_type = max(draw_type, 1)
        
        for start_rank_val in range(ACE_R, THREE_R, -1): 
            potential_straight_ranks = set(range(start_rank_val - 4, start_rank_val + 1))
            present_in_potential = rank_set.intersection(potential_straight_ranks)
            missing_in_potential = potential_straight_ranks - present_in_potential
            if len(present_in_potential) >= 3:
                if len(missing_in_potential) == 0: continue
                elif len(missing_in_potential) == 1:
                    needed = missing_in_potential.pop()
                    if needed == start_rank_val - 4 or needed == start_rank_val: draw_type = max(draw_type, 2)
                    else: draw_type = max(draw_type, 1)
                    needed_ranks.add(needed)
                elif len(missing_in_potential) == 2 and len(present_in_potential) == 3:
                    needed_ranks.update(missing_in_potential); draw_type = max(draw_type, 1)
        
        needed_ranks.difference_update(rank_set)
        if not needed_ranks: draw_type = 0
        return draw_type, needed_ranks

    @staticmethod
    def _get_straight_draw_outs(needed_ranks: Set[int], board_cards: Set[int], remaining_deck: Set[int]) -> Set[int]:
        outs = set()
        for card in remaining_deck:
            if Card.get_rank_int(card) in needed_ranks and card not in board_cards:
                outs.add(card)
        return outs

    @staticmethod
    def _estimate_row_potential(row_name: str, row_cards: List[int],
                                board_cards_after: Set[int], remaining_deck: Set[int],
                                top_row_for_fl_check: Optional[List[int]] = None) -> float:
        potential_score = 0.0; num_cards_in_row = len(row_cards)
        if row_name not in PlayerBoard.ROW_CAPACITY:
            logger.error(f"Invalid row_name '{row_name}' in _estimate_row_potential."); return 0.0
        row_capacity = PlayerBoard.ROW_CAPACITY[row_name]
        
        if num_cards_in_row == 0: return 0.0
        if num_cards_in_row == row_capacity:
            rank, hand_class_completed, _ = get_hand_rank_safe(row_cards)
            if rank != WORST_RANK: potential_score = (WORST_RANK - rank) * 0.1
            if row_name == "bottom" and hand_class_completed == 8: # Pair
                    potential_score += BOTTOM_ROW_PAIR_BONUS
            return potential_score

        f_suit, f_count = MCTSNode._detect_flush_draw(row_cards)
        if f_suit is not None:
            f_outs = len(MCTSNode._get_flush_draw_outs(f_suit, board_cards_after, remaining_deck))
            f_weight = ROW_FLUSH_DRAW_OUT_WEIGHT * 1.2 if row_name == "bottom" else ROW_FLUSH_DRAW_OUT_WEIGHT
            potential_score += f_outs * f_weight

        s_type, s_needed_ranks = MCTSNode._detect_straight_draw(row_cards)
        if s_type > 0:
            s_outs = len(MCTSNode._get_straight_draw_outs(s_needed_ranks, board_cards_after, remaining_deck))
            s_weight_oesd = ROW_STRAIGHT_DRAW_OUT_WEIGHT * 1.2 if row_name == "bottom" else ROW_STRAIGHT_DRAW_OUT_WEIGHT
            s_weight_gutshot = ROW_GUTSHOT_DRAW_OUT_WEIGHT * 1.2 if row_name == "bottom" else ROW_GUTSHOT_DRAW_OUT_WEIGHT
            if s_type == 2: potential_score += s_outs * s_weight_oesd
            elif s_type == 1: potential_score += s_outs * s_weight_gutshot
        
        ranks = [Card.get_rank_int(c) for c in row_cards]; rank_counts = Counter(ranks)
        for r, count in rank_counts.items():
            if count == 2:
                pair_outs_count = len({c for c in remaining_deck if Card.get_rank_int(c) == r and c not in board_cards_after})
                potential_score += pair_outs_count * ROW_PAIR_OUTS_WEIGHT
            elif count == 3:
                trips_outs_count = len({c for c in remaining_deck if Card.get_rank_int(c) == r and c not in board_cards_after})
                potential_score += trips_outs_count * ROW_TRIPS_OUTS_WEIGHT
        
        for card_val in row_cards: potential_score += Card.get_rank_int(card_val) * ROW_HIGH_CARD_WEIGHT

        if row_name == "middle" and top_row_for_fl_check and len(top_row_for_fl_check) > 0:
            top_ranks_int = [Card.get_rank_int(c) for c in top_row_for_fl_check]
            # Определяем минимальный ранг пары на топе для Фантазии (QQ)
            min_fl_top_pair_rank = RANK_QUEEN 
            # Если на топе уже есть что-то сильнее QQ, берем это за основу
            # (например, если на топе К, то цель КК, если А - то АА)
            current_top_potential_fl_rank = -1
            if any(r == RANK_QUEEN for r in top_ranks_int): current_top_potential_fl_rank = max(current_top_potential_fl_rank, RANK_QUEEN)
            if any(r == RANK_KING for r in top_ranks_int): current_top_potential_fl_rank = max(current_top_potential_fl_rank, RANK_KING)
            if any(r == RANK_ACE for r in top_ranks_int): current_top_potential_fl_rank = max(current_top_potential_fl_rank, RANK_ACE)

            if current_top_potential_fl_rank != -1:
                # Ищем ауты на мидле для пар СТАРШЕ чем current_top_potential_fl_rank
                # или на сеты/стриты/флеши, которые побьют эту пару на топе
                for r_mid_card_val in ranks: # Карты уже на мидле
                    if r_mid_card_val > current_top_potential_fl_rank: # Если карта на мидле старше потенциальной пары на топе
                        support_outs = len({c for c in remaining_deck if Card.get_rank_int(c) == r_mid_card_val and c not in board_cards_after})
                        potential_score += support_outs * FANTASY_SUPPORT_OUT_WEIGHT
                # Можно добавить оценку аутов на новые карты, которые могут прийти на мидл
                # и образовать пару старше, чем на топе.
                for r_deck_card_val in INT_RANKS: # Перебираем все возможные ранги
                    if r_deck_card_val > current_top_potential_fl_rank:
                        # Сколько таких карт в колоде
                        num_such_cards_in_deck = len({c for c in remaining_deck if Card.get_rank_int(c) == r_deck_card_val and c not in board_cards_after})
                        if num_such_cards_in_deck > 0:
                             # Даем небольшой бонус за каждый такой аут (например, 1/4 от FANTASY_SUPPORT_OUT_WEIGHT)
                             # так как нужна еще одна такая же карта, чтобы собрать пару.
                             potential_score += num_such_cards_in_deck * (FANTASY_SUPPORT_OUT_WEIGHT * 0.25)
        return potential_score

    @staticmethod
    def _score_placement_v2(board: PlayerBoard, placement_info: Dict[str, Any], remaining_deck: Set[int]) -> float:
        score = 0.0; temp_board = board.copy(); placements = placement_info.get('placements', [])
        initial_cards_on_board = board.get_total_cards()
        is_first_street = (initial_cards_on_board == 0 and len(placements) == 5)

        valid_placement = True
        for card, row, idx in placements:
            if not temp_board.add_card(card, row, idx): valid_placement = False; break
        if not valid_placement: return HEURISTIC_FOUL_PENALTY - 1000
        
        is_foul = False
        if temp_board.is_complete(): is_foul = check_board_foul(temp_board)
        else:
            try:
                top_cards = temp_board.get_row_cards("top"); mid_cards = temp_board.get_row_cards("middle"); bot_cards = temp_board.get_row_cards("bottom")
                rank_t, class_t = (WORST_RANK, WORST_CLASS)
                if len(top_cards) == PlayerBoard.ROW_CAPACITY['top']: rank_t, class_t, _ = get_hand_rank_safe(top_cards)
                rank_m, class_m = (WORST_RANK, WORST_CLASS)
                if len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle']: rank_m, class_m, _ = get_hand_rank_safe(mid_cards)
                rank_b, class_b = (WORST_RANK, WORST_CLASS)
                if len(bot_cards) == PlayerBoard.ROW_CAPACITY['bottom']: rank_b, class_b, _ = get_hand_rank_safe(bot_cards)
                if len(top_cards) == PlayerBoard.ROW_CAPACITY['top'] and len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle']:
                    if rank_t!=WORST_RANK and rank_m!=WORST_RANK and ((class_t < class_m) or (class_t == class_m and rank_t < rank_m)): is_foul = True
                if not is_foul and len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle'] and len(bot_cards) == PlayerBoard.ROW_CAPACITY['bottom']:
                     if rank_m!=WORST_RANK and rank_b!=WORST_RANK and ((class_m < class_b) or (class_m == class_b and rank_m < rank_b)): is_foul = True
            except Exception as e_foul_check: logger.warning(f"Exception during partial foul check: {e_foul_check}"); is_foul = True
        if is_foul: return HEURISTIC_FOUL_PENALTY
        
        board_cards_after = temp_board.get_all_cards(); row_scores = {}; is_fl_qualify_hand_placed = False; fl_top_strength_score = 0.0
        top_row_for_fl_check_cards = temp_board.get_row_cards("top")

        for row_name in PlayerBoard.ROW_NAMES:
            row_cards = temp_board.get_row_cards(row_name); num_cards_in_row = len(row_cards)
            row_capacity = PlayerBoard.ROW_CAPACITY[row_name]; current_row_score = 0.0; row_strength_score = -1.0
            if num_cards_in_row == 0: row_scores[row_name] = 0.0; continue
            
            if num_cards_in_row == row_capacity:
                rank, hand_class, type_str = get_hand_rank_safe(row_cards)
                if rank != WORST_RANK:
                    royalty = get_row_royalty(row_cards, row_name)
                    current_row_score += royalty * ROW_COMPLETED_HAND_WEIGHT
                    row_strength_score = (WORST_RANK - rank) * 0.1
                    if row_name == "top":
                        is_fl_qualify = False
                        if hand_class == 6: is_fl_qualify = True
                        elif hand_class == 8:
                            ranks_in_top = [Card.get_rank_int(c) for c in row_cards]
                            pair_rank_val = next((r_val for r_val, count in Counter(ranks_in_top).items() if count == 2), -1)
                            if pair_rank_val >= RANK_QUEEN: is_fl_qualify = True
                        if is_fl_qualify:
                            is_fl_qualify_hand_placed = True; fl_top_strength_score = row_strength_score
                            current_row_score += HEURISTIC_FL_QUALIFY_BONUS
                    elif row_name == "bottom":
                        if hand_class <= 2: current_row_score += HEURISTIC_FL_REPEAT_BONUS
                        elif hand_class == 8: current_row_score += BOTTOM_ROW_PAIR_BONUS
                    elif row_name == "middle" and hand_class == 6:
                         current_row_score += HEURISTIC_FL_REPEAT_BONUS 
            else:
                potential_score = MCTSNode._estimate_row_potential(
                    row_name, row_cards, board_cards_after, remaining_deck,
                    top_row_for_fl_check=(top_row_for_fl_check_cards if row_name == "middle" else None)
                )
                current_row_score += potential_score; row_strength_score = potential_score
            
            row_scores[row_name] = row_strength_score; score += current_row_score

        if is_first_street:
            bottom_row_cards_fs = temp_board.get_row_cards("bottom")
            if len(bottom_row_cards_fs) == PlayerBoard.ROW_CAPACITY['bottom']:
                _, class_b_fs, _ = get_hand_rank_safe(bottom_row_cards_fs)
                if class_b_fs <= 6: # Трипс или лучше (SF=1, 4oak=2, FH=3, Flush=4, Str=5, 3oak=6)
                    score += FIRST_STREET_STRONG_HAND_ON_BOTTOM_BONUS
        
        # Штраф за пустой низ, если можно было положить пару из текущего хода
        # и если это не первая улица (на первой улице можно строить топ/мид сначала)
        if not is_first_street and not temp_board.get_row_cards("bottom") and \
           (temp_board.get_row_cards("middle") or temp_board.get_row_cards("top")):
            placed_cards_in_current_move = [p[0] for p in placements] # Карты, которые были в placement_info
            ranks_in_placed_cards = Counter(Card.get_rank_int(c) for c in placed_cards_in_current_move)
            had_pair_to_place_on_bottom = any(count >= 2 for count in ranks_in_placed_cards.values())
            
            # Проверяем, что ни одна из карт текущего хода не пошла на боттом
            bottom_is_empty_and_no_cards_placed_there = True
            for _, r_name, _ in placements:
                if r_name == "bottom":
                    bottom_is_empty_and_no_cards_placed_there = False; break
            
            if had_pair_to_place_on_bottom and bottom_is_empty_and_no_cards_placed_there:
                score += EMPTY_BOTTOM_PENALTY
                
        if is_fl_qualify_hand_placed:
            potential_middle = row_scores.get("middle", -1.0); potential_bottom = row_scores.get("bottom", -1.0); risk_penalty = 0.0
            if potential_middle >= fl_top_strength_score * 0.8: risk_penalty += (potential_middle - fl_top_strength_score * 0.8) * HEURISTIC_FL_RISK_PENALTY_FACTOR
            if potential_bottom >= potential_middle * 1.0: risk_penalty += (potential_bottom - potential_middle * 1.0) * HEURISTIC_FL_RISK_PENALTY_FACTOR
            if risk_penalty < 0: score += risk_penalty
        return score

    @staticmethod
    def _choose_best_heuristic_placement_v2(board: PlayerBoard, cards_dealt: List[int], remaining_deck: Set[int]) -> Optional[Dict[str, Any]]:
        num_on_board = board.get_total_cards(); num_to_place = 5 if num_on_board == 0 else 2
        num_to_discard = 0 if num_on_board == 0 else 1; available_slots = board.get_available_slots()
        if len(available_slots) < num_to_place: return None
        best_placement_info: Optional[Dict[str, Any]] = None; best_score = -float('inf') - 1.0
        cards_to_place_options: List[Tuple[List[int], Optional[int]]] = []
        if num_to_discard == 0:
            if len(cards_dealt) == num_to_place: cards_to_place_options.append((cards_dealt, None))
            else: return None
        else:
            if len(cards_dealt) != 3: return None
            for i in range(3):
                discard_card = cards_dealt[i]; place_cards = [cards_dealt[j] for j in range(3) if i != j]
                cards_to_place_options.append((place_cards, discard_card))
        
        # Ограничим количество перестановок и комбинаций слотов для производительности
        MAX_PERMUTATIONS_STREET_1 = 24 # Для 5 карт (5! = 120, 4! = 24)
        MAX_PERMUTATIONS_STREET_N = 2  # Для 2 карт (2! = 2)
        MAX_SLOT_COMBINATIONS_STREET_1 = 50 # C(13,5) много, C(8,5) = 56. C(13,2) ~78
        MAX_SLOT_COMBINATIONS_STREET_N = 100 # C(8,2)=28, C(13,2)=78

        current_max_perms = MAX_PERMUTATIONS_STREET_1 if num_to_place == 5 else MAX_PERMUTATIONS_STREET_N
        current_max_slot_combos = MAX_SLOT_COMBINATIONS_STREET_1 if num_to_place == 5 else MAX_SLOT_COMBINATIONS_STREET_N
        
        evaluated_count = 0
        for cards_to_place, discarded_card in cards_to_place_options:
            perm_count = 0
            for card_permutation in permutations(cards_to_place):
                perm_count += 1;
                if perm_count > current_max_perms: break
                slot_combo_count = 0
                for slot_combination in combinations(available_slots, num_to_place):
                    slot_combo_count += 1;
                    if slot_combo_count > current_max_slot_combos: break
                    current_placements: List[Tuple[int, str, int]] = []
                    for i in range(num_to_place): current_placements.append((card_permutation[i], slot_combination[i][0], slot_combination[i][1]))
                    placement_info = {'placements': current_placements, 'discarded': discarded_card}
                    score = MCTSNode._score_placement_v2(board, placement_info, remaining_deck); evaluated_count += 1
                    if score >= best_score: # Используем >= чтобы предпочесть более поздние варианты при равном счете (небольшая рандомизация)
                        if score > HEURISTIC_FOUL_PENALTY + 1: # Убедимся, что это не просто штраф за фол
                             best_score = score; best_placement_info = placement_info
        
        if best_placement_info is None and evaluated_count > 0:
             logger.warning(f"Heuristic v2.8.1: No valid (non-foul) placement found. Evaluated {evaluated_count}. Best score was {best_score}");
        # elif best_placement_info:
        #      logger.debug(f"Heuristic v2.8.1: Best placement score: {best_score:.2f} after {evaluated_count} evals.")

        return best_placement_info

    @staticmethod
    def heuristic_rollout_simulation_v2(initial_board: PlayerBoard, initial_remaining_deck: Set[int]) -> Tuple[float, List[Dict[str, Any]]]:
        actions_history: List[Dict[str, Any]] = []
        try:
            current_board = initial_board.copy(); deck_sim_list = list(initial_remaining_deck); random.shuffle(deck_sim_list); deck_sim_set = set(deck_sim_list)
            while not current_board.is_complete():
                num_cards_on_board = current_board.get_total_cards(); num_to_deal = 3 if num_cards_on_board > 0 else 5
                if len(deck_sim_list) < num_to_deal: return 0.0, actions_history
                dealt_cards = [deck_sim_list.pop() for _ in range(num_to_deal)]; deck_sim_set.difference_update(dealt_cards)
                best_action = MCTSNode._choose_best_heuristic_placement_v2(current_board, dealt_cards, deck_sim_set)
                if best_action is None: return 0.0, actions_history
                placements = best_action.get('placements', []); valid_placement = True
                for card, row, idx in placements:
                    if not current_board.add_card(card, row, idx):
                        logger.error(f"Heuristic v2.8.1: Failed to apply placement {Card.to_str(card)}@{row}[{idx}]"); valid_placement = False; break
                if not valid_placement: return 0.0, actions_history
                actions_history.append(best_action)
            is_foul = check_board_foul(current_board)
            if is_foul: return 0.0, actions_history
            total_royalty = sum(get_row_royalty(current_board.get_row_cards(r), r) for r in PlayerBoard.ROW_NAMES)
            final_fantasy_bonus = 0.0; top_row_cards = current_board.get_row_cards("top")
            if len(top_row_cards) == 3:
                 rank_t, class_t, type_t = get_hand_rank_safe(top_row_cards)
                 if rank_t != WORST_RANK:
                     is_fantasy_hand = False
                     if class_t == 6: is_fantasy_hand = True
                     elif class_t == 8:
                         ranks_in_top = [Card.get_rank_int(c) for c in top_row_cards]
                         pair_rank_val = next((r_val for r_val, count in Counter(ranks_in_top).items() if count == 2), -1)
                         if pair_rank_val >= RANK_QUEEN: is_fantasy_hand = True
                     if is_fantasy_hand: final_fantasy_bonus = FANTASY_BONUS
            final_score = total_royalty + final_fantasy_bonus
            return final_score, actions_history
        except Exception as e:
            logger.error(f"Error during heuristic rollout simulation v2.8.1: {e}", exc_info=True)
            return 0.0, actions_history

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        best_score = -float('inf'); best_child = None; parent_visits = self.visits
        if parent_visits == 0 or not self.children: return random.choice(list(self.children.values())) if self.children else None
        parent_visits_log = math.log(parent_visits + 1e-6)
        items = list(self.children.items()); random.shuffle(items)
        beta = math.sqrt(RAVE_K / (3 * parent_visits + RAVE_K))
        for placement_key, child in items:
            child_visits = child.visits; score = 0.0
            if child_visits == 0:
                if child.rave_visits > 0:
                    rave_score = child.rave_reward / child.rave_visits
                    score = beta * rave_score + exploration_constant * math.sqrt(parent_visits_log / (child_visits + 1e-6))
                else: score = 1e6 + random.random()
            else:
                node_score = child.total_reward / child_visits
                rave_score = child.rave_reward / child.rave_visits if child.rave_visits > 0 else node_score
                combined_score = (1.0 - beta) * node_score + beta * rave_score
                explore_term = exploration_constant * math.sqrt(parent_visits_log / child_visits)
                score = combined_score + explore_term
            if score > best_score: best_score = score; best_child = child
        if best_child is None and items:
             logger.warning(f"UCT selection resulted in None for node {self}. Choosing random child."); best_child = random.choice([c for _, c in items])
        return best_child

    def backpropagate(self, reward: float):
        node: Optional[MCTSNode] = self
        while node is not None: node.visits += 1; node.total_reward += reward; node = node.parent

    def backpropagate_rave(self, simulation_actions: List[Dict[str, Any]], reward: float):
        sim_action_keys: Set[Tuple[Tuple[int, str, int], ...]] = set()
        for action in simulation_actions:
            placements = action.get('placements')
            if placements:
                 try:
                     action_key = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placements]))
                     sim_action_keys.add(action_key)
                 except Exception as e: logger.warning(f"RAVE Backprop Key Error: {e}")
        if not sim_action_keys: return
        node: Optional[MCTSNode] = self
        while node is not None:
            for child_key, child_node in node.children.items():
                if child_key in sim_action_keys: child_node.rave_visits += 1; child_node.rave_reward += reward
            node = node.parent

    def __repr__(self):
        q_val = self.total_reward / self.visits if self.visits > 0 else 0.0
        rave_q_val = self.rave_reward / self.rave_visits if self.rave_visits > 0 else 0.0
        action_str = "Root"
        if self.placement_info and self.placement_info.get('placements'):
             p_list = self.placement_info['placements']
             action_str = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_list])
             if self.placement_info.get('discarded'): action_str += f" (D: {Card.to_str(self.placement_info['discarded'])})"
        untried_count = len(self.untried_next_states) if self.untried_next_states is not None else 'N/A'
        return (f"[Node V={self.visits} R={q_val:.2f} RV={self.rave_visits} RR={rave_q_val:.2f} "
                f"NChild={len(self.children)} UStates={untried_count} Act={action_str}]")
