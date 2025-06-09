# mcts_node.py v2.19 (ULTRATHINK FINAL FIX: Discard-First Logic & Heuristic Rebalance)
"""
Узел MCTS и логика симуляции для OFC Pineapple.
- ULTRATHINK FINAL FIX: Полностью переписана логика генерации ходов для улиц
  со сбросом. Теперь ИИ сначала перебирает варианты сброса, а затем ищет
  лучшее размещение для оставшихся карт. Это решает проблему неверного выбора
  сброса.
- ULTRATHINK FINAL FIX: Сбалансированы эвристические константы (бонус за
  Фантазию и т.д.), чтобы ИИ не принимал излишне рискованные решения в погоне
  за роялти, что исправляет оставшиеся тесты на фол.
"""
import random
import math
import logging
import sys
from typing import List, Tuple, Dict, Optional, Set, Any, cast
from collections import Counter, defaultdict
import itertools

try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, card_to_str, CARD_PLACEHOLDER, STR_RANKS, UNKNOWN_CARD_MARKER_LOGIC, RANK_ACE
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, ROYALTY_MIDDLE_POINTS, ROYALTY_BOTTOM_POINTS, calculate_total_royalty_for_board,
        HAND_TYPE_PAIR_3, HAND_TYPE_TRIPS_3,
        RANK_QUEEN, RANK_KING, RANK_ACE
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD
except ImportError:
    # Mock objects remain the same
    logging.critical("Failed to import modules in mcts_node.py")
    class PlayerBoard: pass # type: ignore
    class Card: pass # type: ignore
    class Deck: pass # type: ignore
    # ... other mocks
    raise

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

RAVE_K: float = 500.0
PW_C: float = 2.0
PW_ALPHA: float = 0.5

# ULTRATHINK FINAL FIX: Re-balanced heuristic constants
HEURISTIC_FOUL_PENALTY = -1000.0
SIMULATION_FOUL_PENALTY = -2000.0
FANTASY_QUALIFY_BONUS = 25.0  # Reduced to prevent overly greedy plays
ROYALTY_MULTIPLIER = 1.0
MADE_HAND_BONUS = 5.0
FIRST_STREET_STRONG_HAND_BOTTOM_BONUS = 2.0

class MCTSNode:
    # __init__ and other methods up to _calculate_heuristic_score_v2 are unchanged.
    def __init__(self, board: PlayerBoard, remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 placement_info: Optional[Dict[str, Any]] = None,
                 num_unknown_removed_cards: int = 0):
        self.board: PlayerBoard = board
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info
        self.num_unknown_removed_cards: int = num_unknown_removed_cards
        self.children: Dict[Tuple[Tuple[Tuple[int, str, int], ...], Any], MCTSNode] = {}
        self.visits: int = 0; self.total_reward: float = 0.0
        self.rave_visits_count: int = 0; self.rave_total_reward: float = 0.0
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Any]]] = None
        self._generated_states_for_expand: Dict[Tuple[Tuple[Tuple[int, str, int], ...], Any], Tuple[PlayerBoard, Any, Dict[str, Any]]] = {}

    @staticmethod
    def _get_card_props(cards: List[int]) -> Tuple[List[int], List[int], Counter, Counter]:
        if not cards:
            return [], [], Counter(), Counter()
        ranks = [Card.get_rank_int(c) for c in cards]
        suits = [Card.get_suit_int(c) for c in cards]
        rank_counts = Counter(ranks)
        suit_counts = Counter(suits)
        return ranks, suits, rank_counts, suit_counts

    def is_terminal(self) -> bool: return self.board.is_complete()

    def _generate_next_states(self, cards_just_dealt: List[int]) -> List[Tuple[PlayerBoard, Any]]:
        generated_states_for_pw: List[Tuple[PlayerBoard, Any]] = []
        self._generated_states_for_expand.clear()
        num_cards_on_board = self.board.get_total_cards()
        available_slots_count = PlayerBoard.TOTAL_CAPACITY - num_cards_on_board
        num_dealt = len(cards_just_dealt)

        if available_slots_count <= 0 or num_dealt == 0: return []

        if num_cards_on_board == 0:
            num_to_place_on_board = 5
            if num_dealt != 5:
                logger.error(f"GenStates: Street 1, expected 5 cards, but got {num_dealt}. Cannot generate states.")
                return []
        else:
            num_to_discard = 1 if num_dealt > 1 else 0
            num_to_place_on_board = min(num_dealt - num_to_discard, available_slots_count)

        if num_to_place_on_board <= 0 and num_dealt > 0:
            discard_info = tuple(sorted(cards_just_dealt)) if len(cards_just_dealt) > 1 else cards_just_dealt[0]
            action_key = (tuple(), discard_info)
            p_details = {'placements': [], 'discarded': discard_info, 'score': HEURISTIC_FOUL_PENALTY / 2}
            self._generated_states_for_expand[action_key] = (self.board.copy(), discard_info, p_details)
            generated_states_for_pw.append((self.board.copy(), discard_info))
            return generated_states_for_pw
        elif num_to_place_on_board <= 0 and num_dealt == 0: return []

        logger.debug(f"GenStates: Board {num_cards_on_board}, Dealt {num_dealt}, Avail {available_slots_count}. Will place {num_to_place_on_board}.")

        possible_placement_infos = MCTSNode._choose_best_heuristic_placement_v2(
            self.board, cards_just_dealt, self.remaining_deck, num_to_place_on_board, self.num_unknown_removed_cards
        )

        for p_info_dict in possible_placement_infos:
            new_board_state = self.board.copy()
            current_placements = p_info_dict.get('placements', [])
            discarded_card_result = p_info_dict.get('discarded')
            valid_action = True
            for card_int, row_name, slot_idx in current_placements:
                if not new_board_state.add_card(card_int, row_name, slot_idx):
                    valid_action = False; break
            if not valid_action: continue
            placements_tuples = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in current_placements]))
            action_key_discard = tuple(sorted(list(discarded_card_result))) if isinstance(discarded_card_result, tuple) else discarded_card_result
            action_key = (placements_tuples, action_key_discard)
            generated_states_for_pw.append((new_board_state, discarded_card_result))
            self._generated_states_for_expand[action_key] = (new_board_state, discarded_card_result, p_info_dict)

        logger.debug(f"Generated {len(self._generated_states_for_expand)} unique placement actions.")
        return generated_states_for_pw

    def expand(self) -> Optional['MCTSNode']:
        if not self._generated_states_for_expand and not self.untried_next_states: return None
        next_action_key_to_expand: Optional[Tuple[Tuple[Tuple[int, str, int], ...], Any]] = None
        for key_candidate in self._generated_states_for_expand.keys():
            if key_candidate not in self.children: next_action_key_to_expand = key_candidate; break
        if next_action_key_to_expand is None: return None

        board_after_action, discarded_info, placement_info_for_child = self._generated_states_for_expand[next_action_key_to_expand]
        new_deck_for_child = self.remaining_deck.copy()
        for card_int_placed, _, _ in placement_info_for_child.get('placements', []):
            if card_int_placed in new_deck_for_child: new_deck_for_child.remove(card_int_placed)
        if discarded_info is not None:
            if isinstance(discarded_info, tuple):
                for dc_card in discarded_info:
                    if dc_card in new_deck_for_child: new_deck_for_child.remove(dc_card)
            elif discarded_info in new_deck_for_child: new_deck_for_child.remove(discarded_info)

        child_node = MCTSNode(board_after_action.copy(), new_deck_for_child, parent=self,
                              placement_info=placement_info_for_child,
                              num_unknown_removed_cards=self.num_unknown_removed_cards)
        self.children[next_action_key_to_expand] = child_node
        logger.debug(f"Expanded child for action: {next_action_key_to_expand}")
        return child_node

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        if not self.children: return None
        unvisited_children = [child for child in self.children.values() if child.visits == 0]
        if unvisited_children: return random.choice(unvisited_children)

        best_score = -float('inf'); best_children: List[MCTSNode] = []
        parent_visits_log = math.log(self.visits) if self.visits > 1 else 0

        for child_node in self.children.values():
            if child_node.visits == 0: logger.error("UCT: Child with 0 visits after FPU. Should not happen."); return child_node

            q_value = child_node.total_reward / child_node.visits
            ucb_exploration_term = exploration_constant * math.sqrt(parent_visits_log / child_node.visits)

            rave_q_value = 0.0; alpha_rave = 0.0
            if child_node.rave_visits_count > 0:
                alpha_rave = RAVE_K / (RAVE_K + child_node.visits)
                rave_q_value = child_node.rave_total_reward / child_node.rave_visits_count
                score = (1 - alpha_rave) * q_value + alpha_rave * rave_q_value + ucb_exploration_term
            else: score = q_value + ucb_exploration_term

            if score > best_score: best_score = score; best_children = [child_node]
            elif score == best_score: best_children.append(child_node)

        return random.choice(best_children) if best_children else None

    @staticmethod
    def _calculate_heuristic_score_v2(board: PlayerBoard, deck_snapshot: Set[int], is_first_street: bool = False, num_unknown_removed: int = 0, original_deck_size_for_snapshot: int = 0) -> float:
        if board.is_complete():
            if check_board_foul(board):
                return HEURISTIC_FOUL_PENALTY
            return float(calculate_total_royalty_for_board(board))

        score = 0.0
        top_cards = board.get_row_cards('top')
        mid_cards = board.get_row_cards('middle')
        bot_cards = board.get_row_cards('bottom')

        top_rank, top_class, _ = get_hand_rank_safe(top_cards)
        mid_rank, mid_class, _ = get_hand_rank_safe(mid_cards)
        bot_rank, bot_class, _ = get_hand_rank_safe(bot_cards)

        foul_risk_penalty = 0.0
        if len(top_cards) == 3 and len(mid_cards) == 5:
            if (top_class < mid_class) or (top_class == mid_class and top_rank < mid_rank):
                return HEURISTIC_FOUL_PENALTY
        if len(mid_cards) == 5 and len(bot_cards) == 5:
            if (mid_class < bot_class) or (mid_class == bot_class and mid_rank < bot_rank):
                return HEURISTIC_FOUL_PENALTY
        
        if top_class != WORST_CLASS and mid_class != WORST_CLASS:
            if (top_class < mid_class) or (top_class == mid_class and top_rank < mid_rank):
                 foul_risk_penalty += HEURISTIC_FOUL_PENALTY / 2
        if mid_class != WORST_CLASS and bot_class != WORST_CLASS:
            if (mid_class < bot_class) or (mid_class == bot_class and mid_rank < bot_rank):
                 foul_risk_penalty += HEURISTIC_FOUL_PENALTY / 2
        score += foul_risk_penalty

        is_fantasy_qualified = False
        if len(top_cards) == 3:
            royalty = get_row_royalty(top_cards, 'top')
            if royalty > 0:
                score += royalty
                _, hand_type, _ = get_hand_rank_safe(top_cards)
                if hand_type == HAND_TYPE_TRIPS_3:
                    is_fantasy_qualified = True
                elif hand_type == HAND_TYPE_PAIR_3:
                    ranks_list = [Card.get_rank_int(c) for c in top_cards]
                    pair_rank = next((r for r, count in Counter(ranks_list).items() if count == 2), -1)
                    if pair_rank >= RANK_QUEEN:
                        is_fantasy_qualified = True
        if len(mid_cards) == 5:
            score += get_row_royalty(mid_cards, 'middle')
        if len(bot_cards) == 5:
            score += get_row_royalty(bot_cards, 'bottom')

        if is_fantasy_qualified:
            score += FANTASY_QUALIFY_BONUS

        cards_on_board = board.get_total_cards()
        score += MCTSNode._estimate_row_potential_v2(top_cards, 'top', deck_snapshot, cards_on_board)
        score += MCTSNode._estimate_row_potential_v2(mid_cards, 'middle', deck_snapshot, cards_on_board)
        score += MCTSNode._estimate_row_potential_v2(bot_cards, 'bottom', deck_snapshot, cards_on_board)

        if is_first_street:
            if len(bot_cards) > 0:
                if bot_class <= 5 and bot_rank != WORST_RANK:
                    score += FIRST_STREET_STRONG_HAND_BOTTOM_BONUS

        return score

    @staticmethod
    def _estimate_row_potential_v2(cards: List[int], row_name: str, deck: Set[int], cards_on_board: int) -> float:
        n = len(cards)
        capacity = PlayerBoard.ROW_CAPACITY.get(row_name, 0)
        if n == 0 or n == capacity:
            return 0.0

        potential = 0.0
        ranks, suits, rank_counts, suit_counts = MCTSNode._get_card_props(cards)
        
        cards_to_draw = PlayerBoard.TOTAL_CAPACITY - cards_on_board
        if cards_to_draw <= 0 or not deck: return 0.0
        
        if capacity == 5:
            for suit, count in suit_counts.items():
                if count == 4:
                    outs = sum(1 for c in deck if Card.get_suit_int(c) == suit)
                    royalty = ROYALTY_MIDDLE_POINTS.get("Flush", 0) if row_name == 'middle' else ROYALTY_BOTTOM_POINTS.get("Flush", 4)
                    potential += (outs / len(deck)) * royalty * ROYALTY_MULTIPLIER
            
            if list(rank_counts.values()).count(2) == 2:
                pair_ranks = [r for r, c in rank_counts.items() if c == 2]
                outs = sum(1 for c in deck if Card.get_rank_int(c) in pair_ranks)
                royalty = ROYALTY_MIDDLE_POINTS.get("Full House", 0) if row_name == 'middle' else ROYALTY_BOTTOM_POINTS.get("Full House", 6)
                potential += (outs / len(deck)) * royalty * ROYALTY_MULTIPLIER
            elif 3 in rank_counts.values():
                outs = sum(1 for c in deck if Card.get_rank_int(c) not in ranks)
                royalty = ROYALTY_MIDDLE_POINTS.get("Full House", 0) if row_name == 'middle' else ROYALTY_BOTTOM_POINTS.get("Full House", 6)
                potential += (outs / len(deck)) * royalty * ROYALTY_MULTIPLIER * 0.5

        if capacity == 3 and n == 2:
            if ranks[0] == ranks[1]:
                pair_rank = ranks[0]
                outs = sum(1 for c in deck if Card.get_rank_int(c) == pair_rank)
                trip_royalty = get_row_royalty([cards[0], cards[1], next((c for c in deck if Card.get_rank_int(c) == pair_rank), 0)], 'top') if outs > 0 else 0
                potential += (outs / len(deck)) * trip_royalty * ROYALTY_MULTIPLIER
            else:
                for r in ranks:
                    if r >= RANK_QUEEN:
                        outs = sum(1 for c in deck if Card.get_rank_int(c) == r)
                        pair_royalty = get_row_royalty([cards[0], cards[1], next((c for c in deck if Card.get_rank_int(c) == r), 0)], 'top') if outs > 0 else 0
                        if pair_royalty > 0:
                            potential += (outs / len(deck)) * pair_royalty * ROYALTY_MULTIPLIER

        return potential

    # ULTRATHINK FINAL FIX: Complete rewrite of the candidate generation function.
    @staticmethod
    def _choose_best_heuristic_placement_v2(
        current_board: PlayerBoard, cards_to_act_on: List[int], current_deck: Set[int],
        num_to_place_on_board: int, num_unknown_removed_cards: int
    ) -> List[Dict[str, Any]]:
        
        candidate_actions: List[Dict[str, Any]] = []
        is_first_street = (current_board.get_total_cards() == 0 and num_to_place_on_board == 5)
        available_slots = current_board.get_available_slots()

        # Determine placement/discard options
        options: List[Tuple[List[int], Any]] = []
        if num_to_place_on_board == len(cards_to_act_on):
            options.append((cards_to_act_on, None))
        else:
            # Discard-first logic: iterate through each card as a potential discard
            for card_to_discard in cards_to_act_on:
                cards_to_place = [c for c in cards_to_act_on if c != card_to_discard]
                options.append((cards_to_place, card_to_discard))

        # For each option, generate placements
        for cards_to_place, discard_info in options:
            if len(available_slots) < len(cards_to_place):
                continue

            slot_perms = list(itertools.permutations(available_slots, len(cards_to_place)))
            if len(slot_perms) > 120:  # Limit permutations
                slot_perms = random.sample(slot_perms, 120)

            for p_slots in slot_perms:
                temp_board = current_board.copy()
                placements_list: List[Tuple[int, str, int]] = []
                valid_placement = True
                
                for i, card_val in enumerate(cards_to_place):
                    row_val, slot_idx_val = p_slots[i]
                    if not temp_board.add_card(card_val, row_val, slot_idx_val):
                        valid_placement = False
                        break
                    placements_list.append((card_val, row_val, slot_idx_val))
                
                if not valid_placement:
                    continue

                deck_after_action = current_deck.copy()
                for c in cards_to_place: deck_after_action.discard(c)
                if discard_info:
                    deck_after_action.discard(discard_info)

                h_score = MCTSNode._calculate_heuristic_score_v2(temp_board, deck_after_action, is_first_street, num_unknown_removed_cards, len(current_deck))
                candidate_actions.append({'score': h_score, 'placements': placements_list, 'discarded': discard_info})

        if not candidate_actions:
            logger.warning("Heuristic generation found no valid candidate actions.")
            return []
            
        candidate_actions.sort(key=lambda x: x['score'], reverse=True)
        return candidate_actions[:30]


def random_rollout_simulation(board_dict: Dict, deck_list_initial: List[int], num_unknown_sim: int) -> Tuple[float, List[Dict[str, Any]]]:
    sim_board = PlayerBoard()
    try:
        for r, c_strs in board_dict.get('rows', {}).items():
            for i, c_str in enumerate(c_strs):
                if c_str and c_str != CARD_PLACEHOLDER:
                    sim_board.add_card(Card.from_str(c_str), r, i)

        deck_for_sim: Set[int]
        if num_unknown_sim > 0 and len(deck_list_initial) > num_unknown_sim:
            deck_for_sim = set(random.sample(deck_list_initial, len(deck_list_initial) - num_unknown_sim))
        elif num_unknown_sim > 0:
            deck_for_sim = set()
        else:
            deck_for_sim = set(deck_list_initial)
        
        sim_deck_list = list(deck_for_sim)
        random.shuffle(sim_deck_list)

        available_slots = sim_board.get_available_slots()
        cards_to_fill = sim_deck_list[:len(available_slots)]

        for i, (row, idx) in enumerate(available_slots):
            if i < len(cards_to_fill):
                sim_board.add_card(cards_to_fill[i], row, idx)

        if sim_board.is_complete():
            if check_board_foul(sim_board):
                return SIMULATION_FOUL_PENALTY, []
            else:
                return float(calculate_total_royalty_for_board(sim_board)), []
        else:
            return float(calculate_total_royalty_for_board(sim_board)) - 50.0, []

    except Exception as e:
        logger.error(f"Random rollout error: {e}", exc_info=True)
        return SIMULATION_FOUL_PENALTY - 50.0, []


def run_parallel_rollout(board_dict: Dict, deck_list: List[int], num_unknown_removed_cards: int) -> Tuple[float, List[Dict[str, Any]]]:
    return random_rollout_simulation(board_dict, deck_list, num_unknown_removed_cards)
