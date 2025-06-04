# mcts_node.py v2.8.9 (SyntaxError in mock Card fix)
# ИСПРАВЛЕНО: Добавлены определения для MAX_PERMUTATIONS_STREET_1 и MAX_PERMUTATIONS_STREET_N
# ИЗМЕНЕНО: Уровень логгера по умолчанию на INFO
# ИСПРАВЛЕНО: Обращение к CARD_PLACEHOLDER
# ИСПРАВЛЕНО: random.shuffle на set и вызов get_cards() вместо get_remaining_cards()
# ИЗМЕНЕНО: Логика в _choose_best_heuristic_placement_v2 и _calculate_heuristic_score_v2
# ИСПРАВЛЕНО: SyntaxError в заглушке PlayerBoard в блоке except ImportError
# ИСПРАВЛЕНО: Добавлен @staticmethod к _calculate_heuristic_score_v2, _estimate_draw_potential, _choose_best_heuristic_placement_v2
# ИСПРАВЛЕНО: SyntaxError в заглушке Card в блоке except ImportError
"""
Узел MCTS и логика симуляции для OFC Pineapple.
"""
import random
import math
import logging
from typing import List, Tuple, Dict, Optional, Set, Any, cast, Iterable
from collections import Counter, defaultdict
import itertools

try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, card_to_str, CARD_PLACEHOLDER, STR_RANKS
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, calculate_total_royalty_for_board,
        HAND_TYPE_PAIR_3, HAND_TYPE_TRIPS_3
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD
except ImportError:
    logging.critical("Failed to import modules in mcts_node.py")
    
    class PlayerBoard: # type: ignore
        TOTAL_CAPACITY = 13
        ROW_NAMES = ['top', 'middle', 'bottom']
        def __init__(self, rows=None, cards_placed=0): 
            self.rows = rows or {name: [] for name in self.ROW_NAMES}
            self._cards_placed = cards_placed
        def copy(self): 
            new_board = PlayerBoard()
            new_board.rows = {r: list(c) for r, c in self.rows.items()}
            new_board._cards_placed = self._cards_placed
            return new_board
        def add_card(self, card, row, index): pass
        def get_total_cards(self): return self._cards_placed
        def is_complete(self): return self._cards_placed == self.TOTAL_CAPACITY
        def get_available_slots(self) -> List[Tuple[str, int]]: return []
        def get_row_cards(self, row_name: str) -> List[int]: return []
        def __str__(self): return "MockBoard"
        def get_board_state_tuple(self) -> Tuple[Tuple[Optional[int], ...], ...]: 
            return tuple(tuple(self.rows.get(r, [])) for r in self.ROW_NAMES)

    class Card: # type: ignore
        @staticmethod 
        def from_str(s): return 0
        @staticmethod 
        def to_str(c): return "??"
        @staticmethod 
        def get_rank_int(c): return 0
        @staticmethod 
        def get_suit_int(c): return 0
    
    class Deck: # type: ignore
        FULL_DECK_CARDS = set(range(1,53))
        def __init__(self, cards: Optional[Set[int]] = None): 
            self.cards = cards if cards is not None else set()
        def deal(self, num): return []
        def get_remaining_cards(self): return list(self.cards)
        def __len__(self): return len(self.cards)
    
    CARD_PLACEHOLDER = "__"
    STR_RANKS = "23456789TJQKA"

    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    WORST_RANK = 9999; WORST_CLASS = 9
    def check_board_foul(*args): return False
    def get_row_royalty(*args): return 0
    def calculate_total_royalty_for_board(*args): return 0
    RANK_MAP = {rank: i for i, rank in enumerate(STR_RANKS)}
    ROYALTY_TOP_PAIRS = {}
    HAND_TYPE_PAIR_3 = "Pair"; HAND_TYPE_TRIPS_3 = "Trips"
    def card_to_str(c): return Card.to_str(c)
    def hand_to_str(h): return [Card.to_str(c) for c in h]
    
    class MockEvaluator5Card: 
        evaluate = lambda s, c: 9999
        get_rank_class = lambda s, r: 9
        class_to_string = lambda s, rc: "Error"
    evaluator_5card = MockEvaluator5Card()
    
    def evaluate_3_card_ofc(*args): return (9999, "Invalid", "XXX")
    WORST_RANK_3CARD = 999


logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

RAVE_K: float = 500.0
PW_C: float = 2.0
PW_ALPHA: float = 0.5
MAX_PERMUTATIONS_STREET_1: int = 120
MAX_PERMUTATIONS_SLOTS_STREET_1: int = 20
MAX_PERMUTATIONS_STREET_N: int = 30


class MCTSNode:
    @staticmethod
    def _get_dynamic_weights(cards_placed_on_board: int, num_unknown_removed: int) -> Dict[str, float]:
        weights = {
            'fantasy_potential_bonus': 25.0,
            'strong_hand_on_bottom_bonus': 50.0,
            'discard_low_card_bonus': 5.0,
            'draw_potential_multiplier': 1.0,
            'foul_penalty': -1000.0,

            'made_flush_score': 80.0,
            'flush_draw_score_per_out': 2.5,
            'three_to_flush_score_per_out': 1.0,
            'sf_bonus_over_flush': 100.0,

            'made_straight_score': 70.0,
            'open_ended_draw_score_per_out': 2.0,
            'gutshot_draw_score_per_out': 1.0,
            'three_to_open_ended_score_per_out': 0.8,
            'three_to_gutshot_score_per_out': 0.4,

            'four_of_a_kind_score': 150.0,
            'three_of_a_kind_made_score': 40.0,
            'two_pair_made_score': 20.0,
            'pair_made_score': 5.0,
            'pair_made_score_4cards': 2.5,

            'out_to_quads_score': 10.0,
            'out_to_trips_score': 3.0,
            'out_to_full_house_from_trips_score': 1.5,
            'out_to_full_house_from_two_pair_score': 2.0,
            'out_to_pair_score': 0.5,
            'out_to_pair_score_needing_two': 0.25,
        }

        progress = cards_placed_on_board / PlayerBoard.TOTAL_CAPACITY if PlayerBoard.TOTAL_CAPACITY > 0 else 0

        if progress < 0.4:
            weights['fantasy_potential_bonus'] = 35.0
            weights['strong_hand_on_bottom_bonus'] = 60.0
            weights['draw_potential_multiplier'] = 1.2
            weights['made_flush_score'] = 85.0
            weights['sf_bonus_over_flush'] = 110.0
        elif progress < 0.7:
            weights['fantasy_potential_bonus'] = 20.0
            weights['draw_potential_multiplier'] = 1.0
            weights['discard_low_card_bonus'] = 7.0
        else:
            weights['fantasy_potential_bonus'] = 10.0
            weights['draw_potential_multiplier'] = 0.7
            weights['discard_low_card_bonus'] = 10.0
            weights['made_flush_score'] = 75.0
            weights['sf_bonus_over_flush'] = 90.0

        if num_unknown_removed > 2:
            weights['draw_potential_multiplier'] *= 0.85
            weights['flush_draw_score_per_out'] *= 0.9
            weights['open_ended_draw_score_per_out'] *= 0.9
            weights['gutshot_draw_score_per_out'] *= 0.85
        return weights

    def __init__(self, board: PlayerBoard, remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 placement_info: Optional[Dict[str, Any]] = None,
                 num_unknown_removed_cards: int = 0):
        self.board: PlayerBoard = board
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info
        self.num_unknown_removed_cards: int = num_unknown_removed_cards
        self.children: Dict[Tuple, MCTSNode] = {}
        self.visits: int = 0
        self.total_reward: float = 0.0
        self.rave_visits: int = 0
        self.rave_reward: float = 0.0
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Optional[int]]]] = None
        self._generated_states_for_expand: Dict[Tuple, Tuple[PlayerBoard, Optional[int], Dict[str, Any]]] = {}

    def is_terminal(self) -> bool:
        return self.board.is_complete()

    def _generate_next_states(self, cards_just_dealt: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        generated_states: List[Tuple[PlayerBoard, Optional[int]]] = []
        self._generated_states_for_expand = {}
        num_cards_on_board = self.board.get_total_cards()
        num_dealt = len(cards_just_dealt)
        num_to_place_on_board: int

        if num_cards_on_board == 0:
            if num_dealt != 5: logger.error(f"Generate states: Expected 5 cards for street 1, got {num_dealt}"); return []
            num_to_place_on_board = 5
        elif num_cards_on_board < PlayerBoard.TOTAL_CAPACITY - 2 :
            if num_dealt != 3: logger.error(f"Generate states: Expected 3 cards for street 2-4, got {num_dealt}"); return []
            num_to_place_on_board = 2
        elif num_cards_on_board == PlayerBoard.TOTAL_CAPACITY - 2:
             if num_dealt != 3: logger.error(f"Generate states: Expected 3 cards for final street (2 slots), got {num_dealt}"); return []
             num_to_place_on_board = 2
        elif num_cards_on_board == PlayerBoard.TOTAL_CAPACITY - 1:
            logger.warning(f"Generate states: Odd number of cards to place for final street ({PlayerBoard.TOTAL_CAPACITY - num_cards_on_board} slots left). Dealt: {num_dealt}")
            if num_dealt == 3: num_to_place_on_board = 1
            else: return []
        else: return []

        logger.info(
            f"Generate states: Board has {num_cards_on_board} cards, {num_dealt} cards dealt. "
            f"Determined num_to_place_on_board = {num_to_place_on_board}."
        )
        if num_cards_on_board == 11 and num_dealt == 3:
            logger.info(
                f"BUGFIX_TRACE: Handling 11 cards on board (PlayerBoard.TOTAL_CAPACITY - 2), 3 dealt. "
                f"num_to_place_on_board set to: {num_to_place_on_board}"
            )

        available_slots_list = self.board.get_available_slots()
        if len(available_slots_list) < num_to_place_on_board:
            logger.warning(f"Not enough available slots ({len(available_slots_list)}) to place {num_to_place_on_board} cards.")
            return []

        possible_placements_infos = MCTSNode._choose_best_heuristic_placement_v2(
            self.board, cards_just_dealt, self.remaining_deck, num_to_place_on_board, self.num_unknown_removed_cards
        )
        
        for p_info_dict in possible_placements_infos:
            new_board_state = self.board.copy()
            current_placements = p_info_dict['placements']
            discarded_card_result = p_info_dict.get('discarded')
            try:
                for card_int, row_name, slot_idx in current_placements:
                    new_board_state.add_card(card_int, row_name, slot_idx)
                placement_tuples = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in current_placements]))
                action_key = (placement_tuples, discarded_card_result)
                generated_states.append((new_board_state, discarded_card_result))
                self._generated_states_for_expand[action_key] = (new_board_state, discarded_card_result, p_info_dict)
            except ValueError as ve: logger.warning(f"Invalid placement during state generation: {ve} for {p_info_dict}")
            except Exception as e: logger.error(f"Unexpected error during state generation: {e} for {p_info_dict}", exc_info=True)
        
        logger.debug(f"Generated {len(generated_states)} next states for node with {num_cards_on_board} cards, dealt {num_dealt}.")
        return generated_states

    def expand(self) -> Optional['MCTSNode']:
        if not self.untried_next_states and (not self._generated_states_for_expand or all(key in self.children for key in self._generated_states_for_expand)):
            return None
        next_action_key_to_expand: Optional[Tuple] = None
        for key_candidate in self._generated_states_for_expand.keys():
            if key_candidate not in self.children:
                next_action_key_to_expand = key_candidate
                break
        if next_action_key_to_expand is None: return None
        
        board_state, discarded_card, placement_info_for_child = self._generated_states_for_expand[next_action_key_to_expand]
        new_deck = self.remaining_deck.copy()
        if placement_info_for_child:
            for card_int, _, _ in placement_info_for_child.get('placements', []): new_deck.discard(card_int)
            if placement_info_for_child.get('discarded') is not None:
                if isinstance(placement_info_for_child['discarded'], tuple):
                    for dc_card in placement_info_for_child['discarded']: new_deck.discard(dc_card)
                else: new_deck.discard(placement_info_for_child['discarded'])
        child_node = MCTSNode(
            board_state,
            new_deck,
            parent=self,
            placement_info=placement_info_for_child,
            num_unknown_removed_cards=self.num_unknown_removed_cards
        )
        self.children[next_action_key_to_expand] = child_node
        if self.untried_next_states:
            self.untried_next_states = [(b, d) for (b, d) in self.untried_next_states if not (b.get_board_state_tuple() == board_state.get_board_state_tuple() and d == discarded_card)]
        return child_node

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        if not self.children: return None
        best_score = -float('inf'); best_children: List[MCTSNode] = []
        for child_key, child in self.children.items():
            if child.visits == 0: return child
            ucb_score = child.total_reward / child.visits + exploration_constant * math.sqrt(math.log(self.visits) / child.visits)
            rave_score = child.rave_reward / child.rave_visits if child.rave_visits > 0 else 0.0
            alpha = max(0.0, (RAVE_K - self.visits) / RAVE_K)
            score = (1 - alpha) * ucb_score + alpha * rave_score
            if score > best_score: best_score = score; best_children = [child]
            elif score == best_score: best_children.append(child)
        return random.choice(best_children) if best_children else None

    @staticmethod
    def _calculate_heuristic_score_v2(board: PlayerBoard, deck_snapshot: Set[int], is_first_street: bool = False, num_unknown_removed: int = 0, original_deck_size_for_snapshot: int = 0) -> float:
        weights = MCTSNode._get_dynamic_weights(board.get_total_cards(), num_unknown_removed)

        if check_board_foul(board): return weights['foul_penalty']
        score = 0.0; total_royalty = calculate_total_royalty_for_board(board); score += total_royalty * 2.0

        top_cards = board.get_row_cards('top'); mid_cards = board.get_row_cards('middle'); bot_cards = board.get_row_cards('bottom')

        if len(top_cards) == 3:
            try:
                _, _, type_t = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                if type_t == HAND_TYPE_PAIR_3:
                    ranks_top = Counter(Card.get_rank_int(c) for c in top_cards)
                    pair_rank_top = next((r for r, count in ranks_top.items() if count == 2), -1)
                    if pair_rank_top >= RANK_MAP['Q']: score += weights['fantasy_potential_bonus']
                elif type_t == HAND_TYPE_TRIPS_3: score += weights['fantasy_potential_bonus'] + 10
            except ValueError: pass

        if is_first_street and len(bot_cards) == 5:
            rank_b, class_b, type_b = get_hand_rank_safe(bot_cards)
            if class_b == 0: # Straight Flush (includes Royal Flush)
                score += 500.0
            elif class_b <= 5: score += weights['strong_hand_on_bottom_bonus']

        row_potential_mid = 0.0
        if 3 <= len(mid_cards) < 5:
            row_potential_mid = MCTSNode._estimate_row_potential_v2(mid_cards, deck_snapshot, num_unknown_removed, original_deck_size_for_snapshot, weights)
        score += row_potential_mid * weights['draw_potential_multiplier'] * 0.5

        row_potential_bot = 0.0
        if 3 <= len(bot_cards) < 5:
            row_potential_bot = MCTSNode._estimate_row_potential_v2(bot_cards, deck_snapshot, num_unknown_removed, original_deck_size_for_snapshot, weights)
        score += row_potential_bot * weights['draw_potential_multiplier']

        if len(top_cards) == 3:
            is_pair_or_trips_top = False
            try:
                _, _, type_t_str = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                if type_t_str in [HAND_TYPE_PAIR_3, HAND_TYPE_TRIPS_3]: is_pair_or_trips_top = True
            except ValueError: pass
            if not is_pair_or_trips_top:
                for card_int in top_cards:
                    if Card.get_rank_int(card_int) > RANK_MAP['9']: score -= 1.0
        return score

    @staticmethod
    def _get_card_props(cards: List[int]) -> Tuple[List[int], List[int], Counter, Counter]:
        ranks = sorted([Card.get_rank_int(c) for c in cards])
        suits = [Card.get_suit_int(c) for c in cards]
        rank_counts = Counter(ranks)
        suit_counts = Counter(suits)
        return ranks, suits, rank_counts, suit_counts

    @staticmethod
    def _count_specific_outs(deck: Set[int], check_func, num_unknown_removed: int, original_deck_size: int) -> float:
        visible_outs = sum(1 for card_in_deck in deck if check_func(card_in_deck))
        if original_deck_size <= 0 or original_deck_size <= num_unknown_removed:
            return 0.0
        prob_out_is_available = (float(original_deck_size) - num_unknown_removed) / float(original_deck_size)
        effective_outs = visible_outs * prob_out_is_available
        return max(0.0, effective_outs)

    @staticmethod
    def _is_straight_flush_possible(card_ranks: List[int], card_suits: List[int], target_suit: Optional[int]) -> Tuple[bool, List[int]]:
        if target_suit is None: return False, []
        suit_matching_ranks = sorted(list(set(r for i, r in enumerate(card_ranks) if card_suits[i] == target_suit)))
        num_suited_cards = len(suit_matching_ranks)
        if num_suited_cards < 3: return False, []
        if num_suited_cards == 3:
            is_straight = (suit_matching_ranks[2] - suit_matching_ranks[0] == 2 and suit_matching_ranks[1] - suit_matching_ranks[0] == 1)
            if not is_straight and set(suit_matching_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3']}: is_straight = True
            if is_straight: return True, suit_matching_ranks
        elif num_suited_cards == 4:
            is_straight = (suit_matching_ranks[3] - suit_matching_ranks[0] == 3 and suit_matching_ranks[1] - suit_matching_ranks[0] == 1 and suit_matching_ranks[2] - suit_matching_ranks[1] == 1)
            if not is_straight and set(suit_matching_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4']}: is_straight = True
            if is_straight: return True, suit_matching_ranks
        return False, suit_matching_ranks

    @staticmethod
    def _calculate_n_of_a_kind_potential_v2(
        ranks: List[int], rank_counts: Counter, num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]
    ) -> float:
        score = 0.0
        FOUR_OF_A_KIND_SCORE = weights.get('four_of_a_kind_score', 150.0)
        THREE_OF_A_KIND_MADE_SCORE = weights.get('three_of_a_kind_made_score', 40.0)
        TWO_PAIR_MADE_SCORE = weights.get('two_pair_made_score', 20.0)
        PAIR_MADE_SCORE = weights.get('pair_made_score', 5.0)
        PAIR_MADE_SCORE_4CARDS = weights.get('pair_made_score_4cards', 2.5)

        OUT_TO_QUADS_SCORE = weights.get('out_to_quads_score', 10.0)
        OUT_TO_TRIPS_SCORE = weights.get('out_to_trips_score', 3.0)
        OUT_TO_FULL_HOUSE_FROM_TRIPS_SCORE = weights.get('out_to_full_house_from_trips_score', 1.5)
        OUT_TO_FULL_HOUSE_FROM_TWO_PAIR_SCORE = weights.get('out_to_full_house_from_two_pair_score', 2.0)
        OUT_TO_PAIR_SCORE = weights.get('out_to_pair_score', 0.5)
        OUT_TO_PAIR_SCORE_NEEDING_TWO = weights.get('out_to_pair_score_needing_two', 0.25)

        if num_cards == 4:
            for rank, count in rank_counts.items():
                if count == 4: score += FOUR_OF_A_KIND_SCORE; break
                if count == 3:
                    score += THREE_OF_A_KIND_MADE_SCORE
                    def is_quad_out(card_in_deck): return Card.get_rank_int(card_in_deck) == rank
                    outs_to_quads = MCTSNode._count_specific_outs(deck, is_quad_out, num_unknown_removed, original_deck_size)
                    score += outs_to_quads * OUT_TO_QUADS_SCORE
                    other_card_rank = next((r for r in ranks if r != rank), None)
                    if other_card_rank is not None:
                        def is_fh_out(card_in_deck): return Card.get_rank_int(card_in_deck) == other_card_rank
                        outs_to_fh = MCTSNode._count_specific_outs(deck, is_fh_out, num_unknown_removed, original_deck_size)
                        score += outs_to_fh * OUT_TO_FULL_HOUSE_FROM_TRIPS_SCORE
                    break
            if score == 0 and len(rank_counts) == 2 and all(c == 2 for c in rank_counts.values()):
                score += TWO_PAIR_MADE_SCORE
                outs_to_fh = 0
                for r_val in rank_counts.keys():
                    def is_fh_out(card_in_deck): return Card.get_rank_int(card_in_deck) == r_val
                    outs_to_fh += MCTSNode._count_specific_outs(deck, is_fh_out, num_unknown_removed, original_deck_size)
                score += outs_to_fh * OUT_TO_FULL_HOUSE_FROM_TWO_PAIR_SCORE
            if score == 0 and len(rank_counts) == 3:
                 pair_rank = next((r for r,c in rank_counts.items() if c==2), None)
                 if pair_rank is not None:
                    score += PAIR_MADE_SCORE_4CARDS
                    def is_trips_out(card_in_deck): return Card.get_rank_int(card_in_deck) == pair_rank
                    outs_to_trips = MCTSNode._count_specific_outs(deck, is_trips_out, num_unknown_removed, original_deck_size)
                    score += outs_to_trips * OUT_TO_TRIPS_SCORE
                    other_ranks = [r for r in rank_counts.keys() if r != pair_rank]
                    for orank in other_ranks:
                        def is_two_pair_out(card_in_deck): return Card.get_rank_int(card_in_deck) == orank
                        score += MCTSNode._count_specific_outs(deck, is_two_pair_out, num_unknown_removed, original_deck_size) * OUT_TO_PAIR_SCORE
        elif num_cards == 3:
            for rank, count in rank_counts.items():
                if count == 3: score += THREE_OF_A_KIND_MADE_SCORE; break
                if count == 2:
                    score += PAIR_MADE_SCORE
                    def is_trips_out(card_in_deck): return Card.get_rank_int(card_in_deck) == rank
                    outs_to_trips = MCTSNode._count_specific_outs(deck, is_trips_out, num_unknown_removed, original_deck_size)
                    score += outs_to_trips * OUT_TO_TRIPS_SCORE
                    # Pairing the kicker for two pair
                    other_card_rank_3c = next((r for r in ranks if r != rank), None)
                    if other_card_rank_3c is not None:
                        def is_fh_out_3c(card_in_deck): return Card.get_rank_int(card_in_deck) == other_card_rank_3c
                        outs_to_fh_3c = MCTSNode._count_specific_outs(deck, is_fh_out_3c, num_unknown_removed, original_deck_size)
                        score += outs_to_fh_3c * OUT_TO_PAIR_SCORE
                    break
            if score == 0:
                outs_to_any_pair = 0
                for r_val in ranks:
                    def is_pair_out(card_in_deck): return Card.get_rank_int(card_in_deck) == r_val
                    outs_to_any_pair += MCTSNode._count_specific_outs(deck, is_pair_out, num_unknown_removed, original_deck_size)
                score += outs_to_any_pair * OUT_TO_PAIR_SCORE_NEEDING_TWO
        return score

    @staticmethod
    def _calculate_flush_potential_for_row_v2(
        ranks: List[int], suits: List[int], suit_counts: Counter, num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]
    ) -> float:
        score = 0.0
        MADE_FLUSH_SCORE = weights.get('made_flush_score', 80.0)
        SF_BONUS_OVER_FLUSH = weights.get('sf_bonus_over_flush', 100.0)
        THREE_TO_FLUSH_SCORE_PER_OUT = weights.get('three_to_flush_score_per_out', 1.0)
        FLUSH_DRAW_SCORE_PER_OUT = weights.get('flush_draw_score_per_out', 2.5)

        for suit, count in suit_counts.items():
            if num_cards == 4:
                if count == 4:
                    is_sf, _ = MCTSNode._is_straight_flush_possible(ranks, suits, suit)
                    if is_sf: score += MADE_FLUSH_SCORE + SF_BONUS_OVER_FLUSH
                    else: score += MADE_FLUSH_SCORE
                    break
            elif num_cards == 3:
                if count == 3:
                    def is_suit_out(card_in_deck): return Card.get_suit_int(card_in_deck) == suit
                    outs = MCTSNode._count_specific_outs(deck, is_suit_out, num_unknown_removed, original_deck_size)
                    if outs >= 2: score += outs * THREE_TO_FLUSH_SCORE_PER_OUT * 0.5
                    break
            if num_cards == 4 and count == 3:
                def is_suit_out(card_in_deck): return Card.get_suit_int(card_in_deck) == suit
                outs = MCTSNode._count_specific_outs(deck, is_suit_out, num_unknown_removed, original_deck_size)
                score += outs * FLUSH_DRAW_SCORE_PER_OUT
        return score

    @staticmethod
    def _calculate_straight_potential_for_row_v2(
        ranks: List[int], num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]
    ) -> float:
        score = 0.0
        unique_ranks = sorted(list(set(ranks)))

        OPEN_ENDED_DRAW_SCORE_PER_OUT = weights.get('open_ended_draw_score_per_out', 2.0)
        GUTSHOT_DRAW_SCORE_PER_OUT = weights.get('gutshot_draw_score_per_out', 1.0)
        THREE_TO_OPEN_ENDED_SCORE_PER_OUT = weights.get('three_to_open_ended_score_per_out', 0.8)
        THREE_TO_GUTSHOT_SCORE_PER_OUT = weights.get('three_to_gutshot_score_per_out', 0.4)

        if num_cards == 4:
            outs = 0.0 # Use float for outs due to _count_specific_outs
            if len(unique_ranks) == 4:
                is_seq = (unique_ranks[3]-unique_ranks[0] == 3 and unique_ranks[1]-unique_ranks[0]==1 and unique_ranks[2]-unique_ranks[1]==1)

                if is_seq : # Normal OESD like 5-6-7-8
                    low_rank = unique_ranks[0]
                    high_rank = unique_ranks[3]
                    if low_rank > RANK_MAP['2']: # Smallest rank is 2 (0)
                         def is_low_out(card_in_deck): return Card.get_rank_int(card_in_deck) == low_rank -1
                         outs += MCTSNode._count_specific_outs(deck, is_low_out, num_unknown_removed, original_deck_size)
                    # Check for Ace-low straight (A-2-3-4-5), where A is rank 0, 5 is rank 3
                    # If current hand is 2-3-4-5 (ranks 0,1,2,3), needs A (rank 12) or 6 (rank 4)
                    if set(unique_ranks) == {RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4'], RANK_MAP['5']}:
                        def is_ace_out(c): return Card.get_rank_int(c) == RANK_MAP['A']
                        def is_six_out(c): return Card.get_rank_int(c) == RANK_MAP['6']
                        outs += MCTSNode._count_specific_outs(deck, is_ace_out, num_unknown_removed, original_deck_size)
                        # outs for 6 is already covered by high_rank < RANK_MAP['A'] if not wheel

                    if high_rank < RANK_MAP['A']: # Highest rank is A (12)
                         def is_high_out(card_in_deck): return Card.get_rank_int(card_in_deck) == high_rank + 1
                         outs += MCTSNode._count_specific_outs(deck, is_high_out, num_unknown_removed, original_deck_size)

                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['K'], RANK_MAP['Q'], RANK_MAP['J']}: # AKQJ -> T
                    def is_ten_out(card_in_deck): return Card.get_rank_int(card_in_deck) == RANK_MAP['T']
                    outs += MCTSNode._count_specific_outs(deck, is_ten_out, num_unknown_removed, original_deck_size)
                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4']}: # A234 -> 5
                    def is_five_out(card_in_deck): return Card.get_rank_int(card_in_deck) == RANK_MAP['5']
                    outs += MCTSNode._count_specific_outs(deck, is_five_out, num_unknown_removed, original_deck_size)
                score += outs * OPEN_ENDED_DRAW_SCORE_PER_OUT

            # Gutshot logic for 4 cards (if not OESD)
            if outs == 0 and len(unique_ranks) >=3: # Check if it was already scored as OESD
                gut_outs = 0.0
                for combo in itertools.combinations(unique_ranks, 3):
                    sorted_combo = sorted(list(combo))
                    # Check for 3-card OESD within 4 cards (e.g. AKQx needs T or J)
                    if (sorted_combo[1] - sorted_combo[0] == 1 and sorted_combo[2] - sorted_combo[1] == 1):
                        # low out
                        if sorted_combo[0] > RANK_MAP['2'] and (sorted_combo[0]-1) not in unique_ranks :
                             def is_l_out(c): return Card.get_rank_int(c) == sorted_combo[0]-1
                             gut_outs += MCTSNode._count_specific_outs(deck, is_l_out, num_unknown_removed, original_deck_size)
                        # high out
                        if sorted_combo[2] < RANK_MAP['A'] and (sorted_combo[2]+1) not in unique_ranks :
                             def is_h_out(c): return Card.get_rank_int(c) == sorted_combo[2]+1
                             gut_outs += MCTSNode._count_specific_outs(deck, is_h_out, num_unknown_removed, original_deck_size)
                        # Ace-low A23 in AKQ2 needs 4,5 (but A23 is not in AKQ2)
                        # Ace-high TJQ in TJQA needs K,9
                        if set(sorted_combo) == {RANK_MAP['T'], RANK_MAP['J'], RANK_MAP['Q']}: # TJQ
                            if RANK_MAP['K'] not in unique_ranks:
                                def is_k_out(c): return Card.get_rank_int(c) == RANK_MAP['K']
                                gut_outs += MCTSNode._count_specific_outs(deck, is_k_out, num_unknown_removed, original_deck_size)
                            if RANK_MAP['9'] not in unique_ranks:
                                def is_9_out(c): return Card.get_rank_int(c) == RANK_MAP['9']
                                gut_outs += MCTSNode._count_specific_outs(deck, is_9_out, num_unknown_removed, original_deck_size)

                    # Standard 3-card gutshot
                    elif (sorted_combo[1] - sorted_combo[0] == 1 and sorted_combo[2] - sorted_combo[1] == 2):
                        if (sorted_combo[1]+1) not in unique_ranks:
                            def is_gut_out(c): return Card.get_rank_int(c) == sorted_combo[1] + 1
                            gut_outs += MCTSNode._count_specific_outs(deck, is_gut_out, num_unknown_removed, original_deck_size)
                    elif (sorted_combo[1] - sorted_combo[0] == 2 and sorted_combo[2] - sorted_combo[1] == 1):
                        if (sorted_combo[0]+1) not in unique_ranks:
                            def is_gut_out(c): return Card.get_rank_int(c) == sorted_combo[0] + 1
                            gut_outs += MCTSNode._count_specific_outs(deck, is_gut_out, num_unknown_removed, original_deck_size)
                score += gut_outs * GUTSHOT_DRAW_SCORE_PER_OUT
        elif num_cards == 3:
            if len(unique_ranks) == 3 :
                is_connector = (unique_ranks[1] - unique_ranks[0] == 1 and unique_ranks[2] - unique_ranks[1] == 1)
                if is_connector:
                    def check_r_low1(c): return Card.get_rank_int(c) == unique_ranks[0]-1
                    outs_r_low1 = MCTSNode._count_specific_outs(deck, check_r_low1, num_unknown_removed, original_deck_size)
                    def check_r_high1(c): return Card.get_rank_int(c) == unique_ranks[2]+1
                    outs_r_high1 = MCTSNode._count_specific_outs(deck, check_r_high1, num_unknown_removed, original_deck_size)
                    score += (outs_r_low1 + outs_r_high1) * THREE_TO_OPEN_ENDED_SCORE_PER_OUT
                elif (unique_ranks[1] - unique_ranks[0] == 1 and unique_ranks[2] - unique_ranks[1] == 2):
                    def check_gut(c): return Card.get_rank_int(c) == unique_ranks[1]+1
                    outs_gut = MCTSNode._count_specific_outs(deck, check_gut, num_unknown_removed, original_deck_size)
                    score += outs_gut * THREE_TO_GUTSHOT_SCORE_PER_OUT
                elif (unique_ranks[1] - unique_ranks[0] == 2 and unique_ranks[2] - unique_ranks[1] == 1):
                    def check_gut(c): return Card.get_rank_int(c) == unique_ranks[0]+1
                    outs_gut = MCTSNode._count_specific_outs(deck, check_gut, num_unknown_removed, original_deck_size)
                    score += outs_gut * THREE_TO_GUTSHOT_SCORE_PER_OUT
                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3']}:
                    def check_4(c): return Card.get_rank_int(c) == RANK_MAP['4']
                    def check_5(c): return Card.get_rank_int(c) == RANK_MAP['5']
                    outs_4 = MCTSNode._count_specific_outs(deck, check_4, num_unknown_removed, original_deck_size)
                    outs_5 = MCTSNode._count_specific_outs(deck, check_5, num_unknown_removed, original_deck_size)
                    score += (outs_4 + outs_5) * THREE_TO_OPEN_ENDED_SCORE_PER_OUT
                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['K'], RANK_MAP['Q']}:
                    def check_J(c): return Card.get_rank_int(c) == RANK_MAP['J']
                    def check_T(c): return Card.get_rank_int(c) == RANK_MAP['T']
                    outs_J = MCTSNode._count_specific_outs(deck, check_J, num_unknown_removed, original_deck_size)
                    outs_T = MCTSNode._count_specific_outs(deck, check_T, num_unknown_removed, original_deck_size)
                    score += (outs_J + outs_T) * THREE_TO_OPEN_ENDED_SCORE_PER_OUT
        return score

    @staticmethod
    def _estimate_row_potential_v2(current_cards: List[int], deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]) -> float:
        num_cards = len(current_cards)
        if not (3 <= num_cards <= 4):
            return 0.0

        ranks, suits, rank_counts, suit_counts = MCTSNode._get_card_props(current_cards)
        total_potential = 0.0

        n_kind_potential = MCTSNode._calculate_n_of_a_kind_potential_v2(ranks, rank_counts, num_cards, deck, num_unknown_removed, original_deck_size, weights)
        flush_potential = MCTSNode._calculate_flush_potential_for_row_v2(ranks, suits, suit_counts, num_cards, deck, num_unknown_removed, original_deck_size, weights)
        straight_potential = MCTSNode._calculate_straight_potential_for_row_v2(ranks, num_cards, deck, num_unknown_removed, original_deck_size, weights)

        total_potential += n_kind_potential

        is_sf_made = False
        if num_cards == 4 and any(sc == 4 for sc in suit_counts.values()):
            major_suit = next(s for s,c in suit_counts.items() if c==4)
            sf_made_check, _ = MCTSNode._is_straight_flush_possible(ranks, suits, major_suit)
            if sf_made_check: is_sf_made = True

        if not is_sf_made:
            total_potential += flush_potential
            total_potential += straight_potential
        elif is_sf_made:
             total_potential += flush_potential

        return total_potential

    # --- Original Heuristic Functions (to be eventually replaced or refactored) ---
    @staticmethod
    def _estimate_row_potential(current_cards: List[int], deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]) -> float: # MODIFIED
        return MCTSNode._estimate_row_potential_v2(current_cards, deck, num_unknown_removed, original_deck_size, weights)

    @staticmethod
    def _calculate_n_of_a_kind_potential(
        ranks: List[int], rank_counts: Counter, num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]
    ) -> float: # MODIFIED
        return MCTSNode._calculate_n_of_a_kind_potential_v2(ranks, rank_counts, num_cards, deck, num_unknown_removed, original_deck_size, weights)

    @staticmethod
    def _calculate_flush_potential_for_row(
        ranks: List[int], suits: List[int], suit_counts: Counter, num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]
    ) -> float: # MODIFIED
       return MCTSNode._calculate_flush_potential_for_row_v2(ranks, suits, suit_counts, num_cards, deck, num_unknown_removed, original_deck_size, weights)

    @staticmethod
    def _calculate_straight_potential_for_row(
        ranks: List[int], num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]
    ) -> float: # MODIFIED
        return MCTSNode._calculate_straight_potential_for_row_v2(ranks, num_cards, deck, num_unknown_removed, original_deck_size, weights)

    # --- End of Heuristic Stubs ---

    @staticmethod
    def _choose_best_heuristic_placement_v2(
        current_board: PlayerBoard, cards_to_act_on: List[int], current_deck: Set[int], num_to_place_on_board: int, num_unknown_removed_cards: int
    ) -> List[Dict[str, Any]]:
        candidate_actions = []
        num_on_board = current_board.get_total_cards(); num_dealt = len(cards_to_act_on)
        dynamic_weights_for_discard = MCTSNode._get_dynamic_weights(num_on_board, num_unknown_removed_cards)
        available_slots = current_board.get_available_slots()
        is_first_street = (num_on_board == 0 and num_to_place_on_board == 5)
        cards_to_place_options: List[List[int]] = []
        cards_to_discard_options: List[Any] = []
        card_permutations_limit: int; slot_permutations_limit: int

        if num_to_place_on_board == 5:
            if num_dealt != 5: return []
            cards_to_place_options = [cards_to_act_on]; cards_to_discard_options = [None]
            card_permutations_limit = MAX_PERMUTATIONS_STREET_1
            slot_permutations_limit = MAX_PERMUTATIONS_SLOTS_STREET_1
        elif num_to_place_on_board == 2 and num_dealt == 3:
            for i in range(3):
                cards_to_place_options.append([c for c_idx, c in enumerate(cards_to_act_on) if c_idx != i])
                cards_to_discard_options.append(cards_to_act_on[i])
            card_permutations_limit = 2; slot_permutations_limit = MAX_PERMUTATIONS_STREET_N
        elif num_to_place_on_board == 1 and num_dealt == 3:
            for i in range(3):
                cards_to_place_options.append([cards_to_act_on[i]])
                cards_to_discard_options.append(tuple(sorted(c for c_idx, c in enumerate(cards_to_act_on) if c_idx != i)))
            card_permutations_limit = 1; slot_permutations_limit = MAX_PERMUTATIONS_STREET_N
        else: logger.warning(f"Heuristic: Unexpected num_to_place {num_to_place_on_board} or num_dealt {num_dealt}"); return []
        
        if len(available_slots) < num_to_place_on_board: return []

        for i in range(len(cards_to_place_options)):
            current_cards_to_place = cards_to_place_options[i]; current_discard_info = cards_to_discard_options[i]
            card_perms = list(itertools.permutations(current_cards_to_place))
            if len(card_perms) > card_permutations_limit: card_perms = random.sample(card_perms, card_permutations_limit)

            slot_perms_iterable: Iterable[Tuple[Tuple[str, int], ...]]
            if is_first_street:
                bottom_slots = tuple(s for s in available_slots if s[0] == 'bottom')
                middle_slots = tuple(s for s in available_slots if s[0] == 'middle')
                explicit_slot_perms = []
                if len(bottom_slots) == num_to_place_on_board:
                    explicit_slot_perms.append(bottom_slots)
                if len(middle_slots) == num_to_place_on_board:
                    if middle_slots not in explicit_slot_perms:
                         explicit_slot_perms.append(middle_slots)
                all_possible_slot_perms = list(itertools.permutations(available_slots, num_to_place_on_board))
                combined_perms = list(explicit_slot_perms)
                processed_sorted_perms = {tuple(sorted(p)) for p in explicit_slot_perms}
                if len(combined_perms) < slot_permutations_limit:
                    potential_samples = random.sample(all_possible_slot_perms, min(len(all_possible_slot_perms), slot_permutations_limit * 2))
                    for p_samp in potential_samples:
                        if len(combined_perms) >= slot_permutations_limit: break
                        sorted_p_samp = tuple(sorted(p_samp))
                        if sorted_p_samp not in processed_sorted_perms:
                            combined_perms.append(p_samp)
                            processed_sorted_perms.add(sorted_p_samp)
                slot_perms_iterable = combined_perms
            else:
                all_slot_perms = list(itertools.permutations(available_slots, num_to_place_on_board))
                if len(all_slot_perms) > slot_permutations_limit:
                    slot_perms_iterable = random.sample(all_slot_perms, slot_permutations_limit)
                else:
                    slot_perms_iterable = all_slot_perms
                
            for p_cards_tuple in card_perms:
                p_cards = list(p_cards_tuple)
                for p_slots in slot_perms_iterable:
                    temp_board = current_board.copy(); placements_list = []; valid_placement_for_rules = True
                    try:
                        for card_idx in range(num_to_place_on_board):
                            card_val, (row_val, slot_idx_val) = p_cards[card_idx], p_slots[card_idx]
                            temp_board.add_card(card_val, row_val, slot_idx_val)
                            placements_list.append((card_val, row_val, slot_idx_val))
                        if is_first_street:
                            ranks_in_hand = Counter(Card.get_rank_int(c) for c in p_cards)
                            trip_rank_in_hand = next((r for r,c in ranks_in_hand.items() if c >=3), -1)
                            if trip_rank_in_hand != -1 and any(pr == 'top' and Card.get_rank_int(pc) == trip_rank_in_hand for pc, pr, _ in placements_list):
                                valid_placement_for_rules = False
                        if not valid_placement_for_rules: continue
                        
                        deck_after_action = current_deck.copy()
                        for card_val, _, _ in placements_list: deck_after_action.discard(card_val)
                        actual_discard_for_info = None
                        if isinstance(current_discard_info, int):
                            deck_after_action.discard(current_discard_info); actual_discard_for_info = current_discard_info
                        elif isinstance(current_discard_info, tuple):
                            for dc in current_discard_info: deck_after_action.discard(dc)
                            actual_discard_for_info = current_discard_info
                        
                        heuristic_score = MCTSNode._calculate_heuristic_score_v2(temp_board, deck_after_action, is_first_street, num_unknown_removed_cards, len(current_deck))
                        if actual_discard_for_info and num_dealt == 3 and num_to_place_on_board == 2:
                            if isinstance(actual_discard_for_info, int):
                                discard_rank = Card.get_rank_int(actual_discard_for_info)
                                placed_ranks = [Card.get_rank_int(p[0]) for p in placements_list]
                                if all(discard_rank < pr for pr in placed_ranks): heuristic_score += dynamic_weights_for_discard['discard_low_card_bonus']
                        candidate_actions.append({'score': heuristic_score, 'placements': placements_list, 'discarded': actual_discard_for_info})
                    except ValueError: continue
        
        if not candidate_actions: return []
        candidate_actions.sort(key=lambda x: x['score'], reverse=True)
        limit_generated_options = 10 if not is_first_street else 5
        return candidate_actions[:limit_generated_options]

def heuristic_rollout_simulation_v2(board_dict: Dict, deck_list: List[int], num_unknown_removed_cards: int) -> Tuple[float, List[Dict[str, Any]]]:
    current_board = PlayerBoard()
    for r, cards_str_list in board_dict.get('rows', {}).items():
        for i, card_str_val in enumerate(cards_str_list):
            if card_str_val and card_str_val != CARD_PLACEHOLDER:
                try: current_board.add_card(Card.from_str(card_str_val), r, i)
                except ValueError: pass

    actual_deck_cards_for_simulation = set(deck_list)
    if num_unknown_removed_cards > 0 and len(deck_list) > num_unknown_removed_cards:
        try:
            actual_deck_cards_for_simulation = set(random.sample(list(deck_list), len(deck_list) - num_unknown_removed_cards))
        except ValueError:
            logger.warning(f"Rollout: Error sampling for unknown cards ({len(deck_list)} cards, {num_unknown_removed_cards} unknown). Using full deck_list.")
            actual_deck_cards_for_simulation = set(deck_list)
    elif num_unknown_removed_cards > 0 and len(deck_list) <= num_unknown_removed_cards:
            logger.warning(f"Rollout: Few cards in deck_list ({len(deck_list)}) vs num_unknown ({num_unknown_removed_cards}). Simulating empty deck.")
            actual_deck_cards_for_simulation = set()

    deck_sim = Deck(cards=actual_deck_cards_for_simulation)
    simulation_actions_taken: List[Dict[str, Any]] = []

    try:
        while not current_board.is_complete():
            num_on_board = current_board.get_total_cards()
            num_to_deal: int; num_to_place_on_board: int
            if num_on_board == 0: num_to_deal, num_to_place_on_board = 5, 5
            elif num_on_board < PlayerBoard.TOTAL_CAPACITY - 2: num_to_deal, num_to_place_on_board = 3, 2
            elif num_on_board == PlayerBoard.TOTAL_CAPACITY - 2: num_to_deal, num_to_place_on_board = 3, 2
            elif num_on_board == PlayerBoard.TOTAL_CAPACITY - 1: num_to_deal, num_to_place_on_board = 3, 1
            else: break
            if len(deck_sim) < num_to_deal: break
            dealt_cards = deck_sim.deal(num_to_deal)
            if not dealt_cards: break

            deck_sim_set_for_heuristic = set(deck_sim.get_remaining_cards())
            best_actions_list = MCTSNode._choose_best_heuristic_placement_v2(
                current_board, dealt_cards, deck_sim_set_for_heuristic, num_to_place_on_board, num_unknown_removed_cards
            )
            if not best_actions_list: break
            best_action = best_actions_list[0]
            if best_action and best_action.get('placements'):
                action_placements = cast(List[Tuple[int, str, int]], best_action['placements'])
                valid_move = True
                for card_int, row, slot_idx in action_placements:
                    try: current_board.add_card(card_int, row, slot_idx)
                    except ValueError: valid_move = False; break
                if not valid_move: break
                simulation_actions_taken.append(best_action)
            else: break
        if check_board_foul(current_board):
            weights = MCTSNode._get_dynamic_weights(current_board.get_total_cards(), num_unknown_removed_cards)
            final_reward = weights['foul_penalty']
        else: final_reward = float(calculate_total_royalty_for_board(current_board))
    except Exception as e:
        logger.error(f"Error during heuristic rollout simulation: {e}", exc_info=True)
        try:
            weights = MCTSNode._get_dynamic_weights(current_board.get_total_cards(), num_unknown_removed_cards)
            final_reward = weights['foul_penalty'] - 20.0
        except:
            final_reward = -1000.0 - 20.0
    return final_reward, simulation_actions_taken

def run_parallel_rollout(board_dict: Dict, deck_list: List[int], num_unknown_removed_cards: int) -> Tuple[float, List[Dict[str, Any]]]:
    return heuristic_rollout_simulation_v2(board_dict, deck_list, num_unknown_removed_cards)
