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
from typing import List, Tuple, Dict, Optional, Set, Any, cast
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

HEURISTIC_STRONG_HAND_ON_BOTTOM_BONUS = 50.0
HEURISTIC_FANTASY_QUALIFY_BONUS = 25.0
HEURISTIC_DISCARD_LOW_CARD_BONUS = 5.0
HEURISTIC_FOUL_PENALTY = -1000.0

class MCTSNode:
    def __init__(self, board: PlayerBoard, remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 placement_info: Optional[Dict[str, Any]] = None):
        self.board: PlayerBoard = board
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info
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

        available_slots_list = self.board.get_available_slots()
        if len(available_slots_list) < num_to_place_on_board:
            logger.warning(f"Not enough available slots ({len(available_slots_list)}) to place {num_to_place_on_board} cards.")
            return []

        possible_placements_infos = MCTSNode._choose_best_heuristic_placement_v2(
            self.board, cards_just_dealt, self.remaining_deck, num_to_place_on_board
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
        child_node = MCTSNode(board_state, new_deck, parent=self, placement_info=placement_info_for_child)
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
    def _calculate_heuristic_score_v2(board: PlayerBoard, deck_snapshot: Set[int], is_first_street: bool = False) -> float:
        if check_board_foul(board): return HEURISTIC_FOUL_PENALTY
        score = 0.0; total_royalty = calculate_total_royalty_for_board(board); score += total_royalty * 2.0
        top_cards = board.get_row_cards('top'); mid_cards = board.get_row_cards('middle'); bot_cards = board.get_row_cards('bottom')
        if len(top_cards) == 3:
            try:
                _, _, type_t = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                if type_t == HAND_TYPE_PAIR_3:
                    ranks_top = Counter(Card.get_rank_int(c) for c in top_cards)
                    pair_rank_top = next((r for r, count in ranks_top.items() if count == 2), -1)
                    if pair_rank_top >= RANK_MAP['Q']: score += HEURISTIC_FANTASY_QUALIFY_BONUS
                elif type_t == HAND_TYPE_TRIPS_3: score += HEURISTIC_FANTASY_QUALIFY_BONUS + 10
            except ValueError: pass
        if is_first_street and len(bot_cards) == 5:
            rank_b, class_b, type_b = get_hand_rank_safe(bot_cards)
            if class_b <= 5: score += HEURISTIC_STRONG_HAND_ON_BOTTOM_BONUS
        if 3 <= len(mid_cards) < 5 : score += MCTSNode._estimate_row_potential(mid_cards, deck_snapshot) * 0.5
        if 3 <= len(bot_cards) < 5 : score += MCTSNode._estimate_row_potential(bot_cards, deck_snapshot)
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
    def _estimate_draw_potential(current_cards: List[int], deck: Set[int]) -> float:
        potential = 0.0
        if not current_cards or len(current_cards) < 3: return 0.0
        suits = Counter(Card.get_suit_int(c) for c in current_cards)
        for suit_val, count in suits.items():
            if count == 4:
                outs = sum(1 for card_in_deck in deck if Card.get_suit_int(card_in_deck) == suit_val)
                potential += outs * 0.2
            elif count == 3 and len(current_cards) == 3:
                outs = sum(1 for card_in_deck in deck if Card.get_suit_int(card_in_deck) == suit_val)
                if outs >=2 : potential += 1.0
        return potential

    @staticmethod
    def _get_card_props(cards: List[int]) -> Tuple[List[int], List[int], Counter, Counter]:
        ranks = sorted([Card.get_rank_int(c) for c in cards])
        suits = [Card.get_suit_int(c) for c in cards]
        rank_counts = Counter(ranks)
        suit_counts = Counter(suits)
        return ranks, suits, rank_counts, suit_counts

    @staticmethod
    def _count_specific_outs(deck: Set[int], check_func) -> int:
        return sum(1 for card_in_deck in deck if check_func(card_in_deck))

    @staticmethod
    def _calculate_flush_potential_for_row(
        ranks: List[int], suits: List[int], suit_counts: Counter, num_cards: int, deck: Set[int]
    ) -> float:
        score = 0.0
        MADE_FLUSH_SCORE = 80.0
        FLUSH_DRAW_SCORE_PER_OUT = 2.5
        THREE_TO_FLUSH_SCORE_PER_OUT = 1.0 # Needs 2 cards

        for suit, count in suit_counts.items():
            if num_cards == 4:
                if count == 4: # Made Flush
                    # Check for straight flush potential as well
                    # For simplicity, a made flush is a high score. SF would be even higher.
                    is_sf, _ = MCTSNode._is_straight_flush_possible(ranks, suits, suit)
                    if is_sf:
                         # This score will be part of straight flush calculation, avoid double counting significantly
                        score += MADE_FLUSH_SCORE + 100 # Extra for SF
                    else:
                        score += MADE_FLUSH_SCORE
                    break
            elif num_cards == 3:
                if count == 3: # 3 to a flush
                    def is_suit_out(card_in_deck):
                        return Card.get_suit_int(card_in_deck) == suit

                    outs = MCTSNode._count_specific_outs(deck, is_suit_out)
                    # This is for needing 2 cards. Probability is (outs/remaining) * ((outs-1)/(remaining-1))
                    # Heuristic:
                    if outs >= 2: # Need at least 2 outs to make it in 2 cards
                        score += outs * FLUSH_DRAW_SCORE_PER_OUT * 0.5 # Reduced for needing 2 cards
                    break

            # Common for 3 or 4 cards if not made flush yet (e.g. 4 cards, 3 of a suit)
            # This logic is more for when we consider adding ONE more card to the current set
            # The problem asks for potential of current 3 or 4 cards.
            # If current_cards is 4, we are looking for 1 more card.
            # If current_cards is 3, we are looking for 2 more cards.
            # The current _estimate_draw_potential is for ONE more card.

            # Let's refine for 4 cards needing 1 more:
            if num_cards == 4 and count == 3: # 3 of a suit in a 4 card hand (e.g. H H H D)
                def is_suit_out(card_in_deck):
                    return Card.get_suit_int(card_in_deck) == suit
                outs = MCTSNode._count_specific_outs(deck, is_suit_out)
                score += outs * FLUSH_DRAW_SCORE_PER_OUT

        return score

    @staticmethod
    def _is_straight_flush_possible(card_ranks: List[int], card_suits: List[int], target_suit: Optional[int]) -> Tuple[bool, List[int]]:
        if target_suit is None: return False, []

        suit_matching_ranks = sorted(list(set(r for i, r in enumerate(card_ranks) if card_suits[i] == target_suit)))

        num_suited_cards = len(suit_matching_ranks)
        if num_suited_cards < 3: return False, []

        # Check if these `num_suited_cards` (3 or 4) form a straight
        # This is to check for made N-card straight flushes (e.g. a 4-card flush that is also a 4-card straight)
        if num_suited_cards == 3:
            is_straight = (suit_matching_ranks[2] - suit_matching_ranks[0] == 2 and suit_matching_ranks[1] - suit_matching_ranks[0] == 1)
            # Ace-low check for 3 cards: A,2,3
            if not is_straight and set(suit_matching_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3']}:
                is_straight = True
            if is_straight: return True, suit_matching_ranks

        elif num_suited_cards == 4:
            is_straight = (suit_matching_ranks[3] - suit_matching_ranks[0] == 3 and \
                           suit_matching_ranks[1] - suit_matching_ranks[0] == 1 and \
                           suit_matching_ranks[2] - suit_matching_ranks[1] == 1)
            # Ace-low check for 4 cards: A,2,3,4
            if not is_straight and set(suit_matching_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4']}:
                 is_straight = True
            # Broadway check for 4 cards: T,J,Q,K or A,K,Q,J (handled by normal sequence if A is high)
            # No, T,J,Q,K is normal. A,K,Q,J is normal if A is highest.
            # Consider A,T,J,Q -> ranks [0,9,10,11] (Ace, T, J, Q) - not straight
            # Consider A,K,Q,J -> ranks [0,10,11,12] (Ace, J, Q, K if A=0) -> needs sorting [0,10,11,12]
            # If RANK_MAP has A=0, T=8, J=9, Q=10, K=11.  A is 12.
            # STR_RANKS = "23456789TJQKA" -> A is 12
            # A=12, K=11, Q=10, J=9.  {12,11,10,9} sorted is {9,10,11,12} -> diff is 3. (K-T)
            # A=12, 2=0, 3=1, 4=2, 5=3. {12,0,1,2} sorted {0,1,2,12} A-2-3-4 (Wheel)
            # The check `suit_matching_ranks[3] - suit_matching_ranks[0] == 3` handles T-K.
            # For A-5 (A2345), if ranks are A,2,3,4,5 -> Ace is high (12), 2 is 0.
            # Ace (12), Five(3). {3,2,1,0,12} sorted {0,1,2,3,12}. This is correct for wheel.
            # For A,2,3,4, the set check `{RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4']}` is fine.

            if is_straight: return True, suit_matching_ranks

        return False, suit_matching_ranks


    @staticmethod
    def _calculate_straight_potential_for_row(
        ranks: List[int], num_cards: int, deck: Set[int]
    ) -> float:
        score = 0.0
        unique_ranks = sorted(list(set(ranks)))

        MADE_STRAIGHT_SCORE = 70.0
        OPEN_ENDED_DRAW_SCORE_PER_OUT = 2.0
        GUTSHOT_DRAW_SCORE_PER_OUT = 1.0
        THREE_TO_OPEN_ENDED_SCORE_PER_OUT = 0.8 # Needs 2 cards
        THREE_TO_GUTSHOT_SCORE_PER_OUT = 0.4  # Needs 2 cards

        if num_cards == 4:
            # Check for made straight (4 unique ranks, can be part of 5-card straight)
            # This means the 4 cards themselves form a sequence or can with one more card
            # Example: 2-3-4-5 or A-K-Q-J.
            # Check for made straight with current 4 cards (less likely, more for 5 card hands)
            # More practically: 4 to a straight

            # 4 to an open-ended straight (e.g., 5-6-7-8 needs 4 or 9)
            outs = 0
            # Check normal sequence: e.g., 5,6,7,8 -> needs 4 or 9
            if len(unique_ranks) == 4:
                is_seq = (unique_ranks[3]-unique_ranks[0] == 3 and unique_ranks[1]-unique_ranks[0]==1 and unique_ranks[2]-unique_ranks[1]==1) or \
                         (unique_ranks[3]-unique_ranks[1] == 2 and unique_ranks[0] == RANK_MAP['2'] and unique_ranks[1] == RANK_MAP['3'] and unique_ranks[2] == RANK_MAP['4'] and unique_ranks[3] == RANK_MAP['5']) # A234
                if is_seq :
                    low_rank = unique_ranks[0]
                    high_rank = unique_ranks[3]
                    if low_rank > RANK_MAP['A']: # Ace is 0 for this map
                         def is_low_out(card_in_deck): return Card.get_rank_int(card_in_deck) == low_rank -1
                         outs += MCTSNode._count_specific_outs(deck, is_low_out)
                    if high_rank < RANK_MAP['A']:
                         def is_high_out(card_in_deck): return Card.get_rank_int(card_in_deck) == high_rank + 1
                         outs += MCTSNode._count_specific_outs(deck, is_high_out)
                # Check for A-K-Q-J (needs T) or A-2-3-4 (needs 5)
                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['K'], RANK_MAP['Q'], RANK_MAP['J']}:
                    def is_ten_out(card_in_deck): return Card.get_rank_int(card_in_deck) == RANK_MAP['T']
                    outs += MCTSNode._count_specific_outs(deck, is_ten_out)
                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4']}: # Ace low
                    def is_five_out(card_in_deck): return Card.get_rank_int(card_in_deck) == RANK_MAP['5']
                    outs += MCTSNode._count_specific_outs(deck, is_five_out)
                score += outs * OPEN_ENDED_DRAW_SCORE_PER_OUT

            # 4 to a gutshot (e.g., 5-6-8-9 needs 7)
            # Iterate through all combinations of 3 ranks from the 4 unique_ranks
            if len(unique_ranks) >= 3 and outs == 0: # Only if not already open-ended
                for combo in itertools.combinations(unique_ranks, 3):
                    sorted_combo = sorted(list(combo))
                    # Check for gutshot: e.g., 5,6,8 needs 7. Difference of 2 and 1, or 1 and 2.
                    if (sorted_combo[1] - sorted_combo[0] == 1 and sorted_combo[2] - sorted_combo[1] == 2): # 5-6-8 needs 7
                        def is_gut_out(card_in_deck): return Card.get_rank_int(card_in_deck) == sorted_combo[1] + 1
                        outs += MCTSNode._count_specific_outs(deck, is_gut_out)
                    elif (sorted_combo[1] - sorted_combo[0] == 2 and sorted_combo[2] - sorted_combo[1] == 1): # 5-7-8 needs 6
                        def is_gut_out(card_in_deck): return Card.get_rank_int(card_in_deck) == sorted_combo[0] + 1
                        outs += MCTSNode._count_specific_outs(deck, is_gut_out)
                score += outs * GUTSHOT_DRAW_SCORE_PER_OUT


        elif num_cards == 3:
            if len(unique_ranks) == 3 :
                # 3 to open-ended (e.g. 5-6-7 needs 3-4, 4-8, 8-9)
                # 5-6-7 needs (4 and X) or (8 and X) or (4 and 8)
                # This is complex. Let's simplify: If 5-6-7, needs two cards.
                # Outs for first card: 4 or 8. Outs for second card: depends on first.
                # Heuristic:
                is_connector = (unique_ranks[1] - unique_ranks[0] == 1 and unique_ranks[2] - unique_ranks[1] == 1)
                if is_connector: # e.g. 5-6-7
                    needed_low = [unique_ranks[0]-1, unique_ranks[0]-2] # 4, 3
                    needed_high = [unique_ranks[2]+1, unique_ranks[2]+2] # 8, 9
                    needed_middle_low = [unique_ranks[0]-1, unique_ranks[2]+1] # 4,8 (double gutshot if one hits)

                    outs = 0
                    # Card1: rank_low_1 (e.g., 4 for 5-6-7)
                    def check_r_low1(c): return Card.get_rank_int(c) == unique_ranks[0]-1
                    outs_r_low1 = MCTSNode._count_specific_outs(deck, check_r_low1)
                    # Card1: rank_high_1 (e.g., 8 for 5-6-7)
                    def check_r_high1(c): return Card.get_rank_int(c) == unique_ranks[2]+1
                    outs_r_high1 = MCTSNode._count_specific_outs(deck, check_r_high1)

                    # Simplified: count total cards that could help form a straight
                    # This is approximate as it doesn't do conditional probability for 2 cards
                    score += (outs_r_low1 + outs_r_high1) * THREE_TO_OPEN_ENDED_SCORE_PER_OUT

                # 3 to gutshot (e.g. 5-6-8 needs 7, then one more card)
                # Or 5-7-8 needs 6
                elif (unique_ranks[1] - unique_ranks[0] == 1 and unique_ranks[2] - unique_ranks[1] == 2): # 5-6-8 needs 7
                    def check_gut(c): return Card.get_rank_int(c) == unique_ranks[1]+1
                    outs_gut = MCTSNode._count_specific_outs(deck, check_gut)
                    score += outs_gut * THREE_TO_GUTSHOT_SCORE_PER_OUT
                elif (unique_ranks[1] - unique_ranks[0] == 2 and unique_ranks[2] - unique_ranks[1] == 1): # 5-7-8 needs 6
                    def check_gut(c): return Card.get_rank_int(c) == unique_ranks[0]+1
                    outs_gut = MCTSNode._count_specific_outs(deck, check_gut)
                    score += outs_gut * THREE_TO_GUTSHOT_SCORE_PER_OUT
                # Special case A23 for wheel
                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['2'], RANK_MAP['3']}:
                    def check_4(c): return Card.get_rank_int(c) == RANK_MAP['4']
                    def check_5(c): return Card.get_rank_int(c) == RANK_MAP['5']
                    outs_4 = MCTSNode._count_specific_outs(deck, check_4)
                    outs_5 = MCTSNode._count_specific_outs(deck, check_5)
                    score += (outs_4 + outs_5) * THREE_TO_OPEN_ENDED_SCORE_PER_OUT # Approx
                # Special case AKQ for broadway
                elif set(unique_ranks) == {RANK_MAP['A'], RANK_MAP['K'], RANK_MAP['Q']}:
                    def check_J(c): return Card.get_rank_int(c) == RANK_MAP['J']
                    def check_T(c): return Card.get_rank_int(c) == RANK_MAP['T']
                    outs_J = MCTSNode._count_specific_outs(deck, check_J)
                    outs_T = MCTSNode._count_specific_outs(deck, check_T)
                    score += (outs_J + outs_T) * THREE_TO_OPEN_ENDED_SCORE_PER_OUT # Approx
        return score

    @staticmethod
    def _calculate_n_of_a_kind_potential(
        ranks: List[int], rank_counts: Counter, num_cards: int, deck: Set[int]
    ) -> float:
        score = 0.0
        FOUR_OF_A_KIND_SCORE = 150.0
        THREE_OF_A_KIND_MADE_SCORE = 40.0
        TWO_PAIR_MADE_SCORE = 20.0 # for 4 cards
        PAIR_MADE_SCORE = 5.0 # for 3 cards (less valuable than trips)

        OUT_TO_QUADS_SCORE = 10.0 # per out
        OUT_TO_TRIPS_SCORE = 3.0  # per out (from pair)
        OUT_TO_FULL_HOUSE_FROM_TRIPS_SCORE = 1.5 # per out (pairing any other card)
        OUT_TO_FULL_HOUSE_FROM_TWO_PAIR_SCORE = 2.0 # per out (pairing one of the pairs)
        OUT_TO_PAIR_SCORE = 0.5 # per out (from no pair)

        if num_cards == 4:
            for rank, count in rank_counts.items():
                if count == 4: score += FOUR_OF_A_KIND_SCORE; break
                if count == 3:
                    score += THREE_OF_A_KIND_MADE_SCORE
                    # Outs to Quads
                    def is_quad_out(card_in_deck): return Card.get_rank_int(card_in_deck) == rank
                    outs_to_quads = MCTSNode._count_specific_outs(deck, is_quad_out)
                    score += outs_to_quads * OUT_TO_QUADS_SCORE

                    # Outs to Full House (pairing the 4th card)
                    other_card_rank = next((r for r in ranks if r != rank), None)
                    if other_card_rank is not None:
                        def is_fh_out(card_in_deck): return Card.get_rank_int(card_in_deck) == other_card_rank
                        outs_to_fh = MCTSNode._count_specific_outs(deck, is_fh_out)
                        score += outs_to_fh * OUT_TO_FULL_HOUSE_FROM_TRIPS_SCORE
                    break
            # Check for two pair if not quads or trips
            if score == 0 and len(rank_counts) == 2 and all(c == 2 for c in rank_counts.values()): # Two Pair
                score += TWO_PAIR_MADE_SCORE
                # Outs to Full House
                outs_to_fh = 0
                for r_val in rank_counts.keys():
                    def is_fh_out(card_in_deck): return Card.get_rank_int(card_in_deck) == r_val
                    outs_to_fh += MCTSNode._count_specific_outs(deck, is_fh_out)
                score += outs_to_fh * OUT_TO_FULL_HOUSE_FROM_TWO_PAIR_SCORE

            # Check for one pair if nothing else (e.g. AAKQ)
            if score == 0 and len(rank_counts) == 3: # One pair and two kickers
                 pair_rank = next((r for r,c in rank_counts.items() if c==2), None)
                 if pair_rank is not None:
                    score += PAIR_MADE_SCORE * 0.5 # Less than 3-card pair
                    # Outs to trips or two pair
                    def is_trips_out(card_in_deck): return Card.get_rank_int(card_in_deck) == pair_rank
                    outs_to_trips = MCTSNode._count_specific_outs(deck, is_trips_out)
                    score += outs_to_trips * OUT_TO_TRIPS_SCORE

                    other_ranks = [r for r in rank_counts.keys() if r != pair_rank]
                    for orank in other_ranks:
                        def is_two_pair_out(card_in_deck): return Card.get_rank_int(card_in_deck) == orank
                        score += MCTSNode._count_specific_outs(deck, is_two_pair_out) * OUT_TO_PAIR_SCORE


        elif num_cards == 3:
            for rank, count in rank_counts.items():
                if count == 3: score += THREE_OF_A_KIND_MADE_SCORE; break # Made Trips
                if count == 2: # Made Pair
                    score += PAIR_MADE_SCORE
                    # Outs to trips
                    def is_trips_out(card_in_deck): return Card.get_rank_int(card_in_deck) == rank
                    outs_to_trips = MCTSNode._count_specific_outs(deck, is_trips_out)
                    score += outs_to_trips * OUT_TO_TRIPS_SCORE # For one more card
                    # Consider needing 2 cards for FH from this pair: Pair + X + Y -> Need X or Y
                    # This is complex, simplified for now.
                    break
            # If no pair or trips (3 unique ranks)
            if score == 0:
                outs_to_any_pair = 0
                for r_val in ranks: # Ranks are unique here
                    def is_pair_out(card_in_deck): return Card.get_rank_int(card_in_deck) == r_val
                    outs_to_any_pair += MCTSNode._count_specific_outs(deck, is_pair_out)
                score += outs_to_any_pair * OUT_TO_PAIR_SCORE * 0.5 # Reduced for needing 2 cards for pair

        return score

    @staticmethod
    def _estimate_row_potential(current_cards: List[int], deck: Set[int]) -> float:
        num_cards = len(current_cards)
        if not (3 <= num_cards <= 4):
            return 0.0

        ranks, suits, rank_counts, suit_counts = MCTSNode._get_card_props(current_cards)

        total_potential = 0.0

        # N of a kind potential (Pairs, Trips, Quads, Full Houses)
        total_potential += MCTSNode._calculate_n_of_a_kind_potential(ranks, rank_counts, num_cards, deck)

        # Flush potential
        # Note: _calculate_flush_potential already gives high score for made flush.
        # We need to be careful not to massively overscore straight flushes by adding flush + straight scores.
        # The _is_straight_flush_possible is a simple check.
        # A more robust way would be to evaluate the hand using a 5-card evaluator if we assume 1/2 more cards.

        flush_potential = MCTSNode._calculate_flush_potential_for_row(ranks, suits, suit_counts, num_cards, deck)

        # Straight potential
        straight_potential = MCTSNode._calculate_straight_potential_for_row(ranks, num_cards, deck)

        # Straight Flush Potential Check
        # This is tricky to combine. If a made flush was detected, and it was part of SF,
        # flush_potential already includes a bonus.
        # If it's a draw to SF, it's even more complex.
        # For now, let's add scores, but this might need refinement.
        # Example: 3 cards to SF (e.g. 5s6s7s)
        # It has 3-to-flush potential, 3-to-straight potential.
        # And specific outs to SF.

        is_sf_made = False
        if num_cards == 4 and any(sc == 4 for sc in suit_counts.values()): # Potential made flush
            major_suit = next(s for s,c in suit_counts.items() if c==4)
            sf_made_check, _ = MCTSNode._is_straight_flush_possible(ranks, suits, major_suit)
            if sf_made_check:
                is_sf_made = True # Score already boosted in flush potential

        # If not a made SF, add straight and flush potentials.
        # If it IS a made SF, flush_potential already has a large SF bonus.
        # Avoid double counting straight part if SF is made.
        if not is_sf_made:
            total_potential += flush_potential
            total_potential += straight_potential
        elif is_sf_made : # It is a made SF, flush_potential contains the big SF bonus
             total_potential += flush_potential # This already has SF bonus
             # Do not add straight_potential separately if it's a straight flush.

        # TODO: More advanced combination logic might be needed.
        # E.g., if a hand has a 4-flush and a 4-straight, these are often mutually exclusive for the *next* card.
        # However, some draws (like open-ended straight flush draw) contribute to both.
        # For now, summing heuristic scores is a common approach.

        return total_potential

    @staticmethod
    def _choose_best_heuristic_placement_v2(
        current_board: PlayerBoard, cards_to_act_on: List[int], current_deck: Set[int], num_to_place_on_board: int
    ) -> List[Dict[str, Any]]:
        candidate_actions = []
        num_on_board = current_board.get_total_cards(); num_dealt = len(cards_to_act_on)
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
            
            slot_perms = list(itertools.permutations(available_slots, num_to_place_on_board))
            if len(slot_perms) > slot_permutations_limit: slot_perms = random.sample(slot_perms, slot_permutations_limit)
                
            for p_cards_tuple in card_perms:
                p_cards = list(p_cards_tuple)
                for p_slots in slot_perms:
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
                        
                        heuristic_score = MCTSNode._calculate_heuristic_score_v2(temp_board, deck_after_action, is_first_street)
                        if actual_discard_for_info and num_dealt == 3 and num_to_place_on_board == 2:
                            # Убедимся, что actual_discard_for_info это int перед вызовом Card.get_rank_int
                            if isinstance(actual_discard_for_info, int):
                                discard_rank = Card.get_rank_int(actual_discard_for_info)
                                placed_ranks = [Card.get_rank_int(p[0]) for p in placements_list]
                                if all(discard_rank < pr for pr in placed_ranks): heuristic_score += HEURISTIC_DISCARD_LOW_CARD_BONUS
                            else: # Если actual_discard_for_info это кортеж (2 сброшенные карты), эта логика не применяется
                                pass
                        
                        candidate_actions.append({'score': heuristic_score, 'placements': placements_list, 'discarded': actual_discard_for_info})
                    except ValueError: continue
        
        if not candidate_actions: return []
        candidate_actions.sort(key=lambda x: x['score'], reverse=True)
        limit_generated_options = 10 if not is_first_street else 5
        return candidate_actions[:limit_generated_options]

def heuristic_rollout_simulation_v2(board_dict: Dict, deck_list: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    current_board = PlayerBoard()
    for r, cards_str_list in board_dict.get('rows', {}).items():
        for i, card_str_val in enumerate(cards_str_list):
            if card_str_val and card_str_val != CARD_PLACEHOLDER:
                try: current_board.add_card(Card.from_str(card_str_val), r, i)
                except ValueError: pass
    deck_sim = Deck(cards=set(deck_list))
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
            deck_sim_set = set(deck_sim.get_remaining_cards())
            best_actions_list = MCTSNode._choose_best_heuristic_placement_v2(current_board, dealt_cards, deck_sim_set, num_to_place_on_board)
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
        if check_board_foul(current_board): final_reward = HEURISTIC_FOUL_PENALTY
        else: final_reward = float(calculate_total_royalty_for_board(current_board))
    except Exception as e:
        logger.error(f"Error during heuristic rollout simulation: {e}", exc_info=True)
        final_reward = HEURISTIC_FOUL_PENALTY - 20.0
    return final_reward, simulation_actions_taken

def run_parallel_rollout(board_dict: Dict, deck_list: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    return heuristic_rollout_simulation_v2(board_dict, deck_list)
