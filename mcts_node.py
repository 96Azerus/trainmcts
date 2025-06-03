# mcts_node.py v2.8.5 (Deck shuffle and get_cards fix)
# ИСПРАВЛЕНО: Добавлены определения для MAX_PERMUTATIONS_STREET_1 и MAX_PERMUTATIONS_STREET_N
# ИЗМЕНЕНО: Уровень логгера по умолчанию на INFO
# ИСПРАВЛЕНО: Обращение к CARD_PLACEHOLDER
# ИСПРАВЛЕНО: random.shuffle на set и вызов get_cards() вместо get_remaining_cards()
"""
Узел MCTS и логика симуляции для OFC Pineapple.
"""
import random
import math
import logging
from typing import List, Tuple, Dict, Optional, Set, Any, cast
from collections import Counter, defaultdict

try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, card_to_str, CARD_PLACEHOLDER
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, calculate_total_royalty_for_board
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD
except ImportError:
    logging.critical("Failed to import modules in mcts_node.py")
    class PlayerBoard: # type: ignore
        TOTAL_CAPACITY = 13
        def __init__(self, rows=None, cards_placed=0): self.rows = rows or {}; self._cards_placed = cards_placed
        def copy(self): return PlayerBoard(self.rows.copy(), self._cards_placed)
        def add_card(self, card, row, index): pass
        def get_total_cards(self): return self._cards_placed
        def is_complete(self): return self._cards_placed == self.TOTAL_CAPACITY
        def get_available_slots(self): return []
        def get_row_cards(self, row_name: str) -> List[int]: return []
        def __str__(self): return "MockBoard"
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
        def __init__(self, cards=None): self.cards = cards or []
        def deal(self, num): return []
        def get_remaining_cards(self): return [] # Mock for get_remaining_cards
    
    CARD_PLACEHOLDER = "__"

    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    WORST_RANK = 9999; WORST_CLASS = 9
    def check_board_foul(*args): return False
    def get_row_royalty(*args): return 0
    def calculate_total_royalty_for_board(*args): return 0
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {}
    def card_to_str(c): return Card.to_str(c)
    def hand_to_str(h): return [Card.to_str(c) for c in h]
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    def evaluate_3_card_ofc(*args): return (9999, "Invalid", "XXX")
    WORST_RANK_3CARD = 999
    raise ImportError("Missing core logic/evaluator modules for MCTSNode")


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
MAX_PERMUTATIONS_STREET_1: int = 60
MAX_PERMUTATIONS_STREET_N: int = 20


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

        possible_placements_infos: List[Dict[str, Any]] = []
        if num_to_place_on_board == 5:
            heuristic_placement = MCTSNode._choose_best_heuristic_placement_v2(self.board, cards_just_dealt, self.remaining_deck)
            if heuristic_placement: possible_placements_infos.append(heuristic_placement)
            else:
                if len(available_slots_list) >= 5:
                    slots_for_5 = random.sample(available_slots_list, 5)
                    placements = [(cards_just_dealt[i], slots_for_5[i][0], slots_for_5[i][1]) for i in range(5)]
                    possible_placements_infos.append({'placements': placements, 'discarded': None})
        elif num_to_place_on_board == 2 and num_dealt == 3:
            import itertools
            for i in range(num_dealt):
                card_to_discard_val = cards_just_dealt[i]
                cards_to_place_val = [cards_just_dealt[j] for j in range(num_dealt) if j != i]
                if len(available_slots_list) >= 2:
                    slot_permutations = list(itertools.permutations(available_slots_list, 2))
                    for slot_perm in slot_permutations[:MAX_PERMUTATIONS_STREET_N]:
                        placements = [
                            (cards_to_place_val[0], slot_perm[0][0], slot_perm[0][1]),
                            (cards_to_place_val[1], slot_perm[1][0], slot_perm[1][1])
                        ]
                        possible_placements_infos.append({'placements': placements, 'discarded': card_to_discard_val})
        
        for p_info_dict in possible_placements_infos:
            new_board_state = self.board.copy()
            current_placements = p_info_dict['placements']
            discarded_card_result = p_info_dict['discarded']
            try:
                for card_int, row_name, slot_idx in current_placements:
                    new_board_state.add_card(card_int, row_name, slot_idx)
                placement_tuples = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in current_placements]))
                action_key = (placement_tuples, discarded_card_result)
                generated_states.append((new_board_state, discarded_card_result))
                self._generated_states_for_expand[action_key] = (new_board_state, discarded_card_result, p_info_dict)
            except ValueError as ve: logger.warning(f"Invalid placement during state generation: {ve} for {p_info_dict}")
            except Exception as e: logger.error(f"Unexpected error during state generation: {e} for {p_info_dict}", exc_info=True)
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
            if placement_info_for_child.get('discarded') is not None: new_deck.discard(placement_info_for_child['discarded'])
        
        child_node = MCTSNode(board_state, new_deck, parent=self, placement_info=placement_info_for_child)
        self.children[next_action_key_to_expand] = child_node
        
        if self.untried_next_states: # Defensive removal from untried_next_states
            # This part of logic might need review based on how untried_next_states is populated and used
            # For now, a simple filter based on board state and discarded card
            self.untried_next_states = [
                (b, d) for (b, d) in self.untried_next_states 
                if not (b.get_board_state_tuple() == board_state.get_board_state_tuple() and d == discarded_card)
            ]
        return child_node

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        if not self.children: return None
        best_score = -float('inf')
        best_children: List[MCTSNode] = []
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
    def _calculate_heuristic_score_v2(board: PlayerBoard, deck_snapshot: Set[int]) -> float:
        if check_board_foul(board): return -1000.0
        score = 0.0; total_royalty = calculate_total_royalty_for_board(board); score += total_royalty * 2.0
        top_cards = board.get_row_cards('top')
        if len(top_cards) == 3:
            try:
                _, type_str, _ = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                if type_str == "Pair":
                    ranks = Counter(Card.get_rank_int(c) for c in top_cards)
                    pair_rank = next((r for r, count in ranks.items() if count == 2), -1)
                    if pair_rank >= RANK_MAP['Q']: score += 25
                elif type_str == "Trips": score += 35
            except ValueError: pass
        mid_cards = board.get_row_cards('middle'); bot_cards = board.get_row_cards('bottom')
        if 4 <= len(mid_cards) < 5 : score += MCTSNode._estimate_draw_potential(mid_cards, deck_snapshot) * 0.5
        if 4 <= len(bot_cards) < 5 : score += MCTSNode._estimate_draw_potential(bot_cards, deck_snapshot)
        if len(top_cards) == 3:
            is_pair_or_trips = False
            try:
                _, type_str, _ = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                if type_str in ["Pair", "Trips"]: is_pair_or_trips = True
            except ValueError: pass
            if not is_pair_or_trips:
                for card_int in top_cards:
                    if Card.get_rank_int(card_int) > RANK_MAP['9']: score -= 0.5
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
    def _choose_best_heuristic_placement_v2(
        current_board: PlayerBoard, cards_to_act_on: List[int], current_deck: Set[int]
    ) -> Optional[Dict[str, Any]]:
        import itertools
        best_heuristic_score = -float('inf'); candidate_actions = []
        num_on_board = current_board.get_total_cards(); num_dealt = len(cards_to_act_on)
        available_slots = current_board.get_available_slots()

        if num_on_board == 0:
            if num_dealt != 5: return None
            cards_to_place_options = [cards_to_act_on]; cards_to_discard_options = [None]
            num_to_place_on_board = 5; current_max_perms = MAX_PERMUTATIONS_STREET_1
        elif num_dealt == 3:
            cards_to_place_options = []; cards_to_discard_options = []
            for i in range(3):
                cards_to_place_options.append([cards_to_act_on[j] for j in range(3) if j != i])
                cards_to_discard_options.append(cards_to_act_on[i])
            num_to_place_on_board = 2; current_max_perms = MAX_PERMUTATIONS_STREET_N
        else: logger.warning(f"Heuristic: Unexpected num_dealt {num_dealt} for board size {num_on_board}"); return None
        
        if len(available_slots) < num_to_place_on_board: return None

        for i in range(len(cards_to_place_options)):
            current_cards_to_place = cards_to_place_options[i]; current_discard = cards_to_discard_options[i]
            for p_cards in itertools.permutations(current_cards_to_place):
                limited_slot_perms = list(itertools.permutations(available_slots, num_to_place_on_board))
                if len(limited_slot_perms) > current_max_perms:
                    limited_slot_perms = random.sample(limited_slot_perms, current_max_perms)
                for p_slots in limited_slot_perms:
                    temp_board = current_board.copy(); placements_list = []; valid_action = True
                    try:
                        for card_idx in range(num_to_place_on_board):
                            card_val, (row_val, slot_idx_val) = p_cards[card_idx], p_slots[card_idx]
                            temp_board.add_card(card_val, row_val, slot_idx_val)
                            placements_list.append((card_val, row_val, slot_idx_val))
                        if num_on_board == 0 and num_to_place_on_board == 5: # First street trip on top rule
                            ranks_in_hand = Counter(Card.get_rank_int(c) for c in p_cards)
                            trip_rank_in_hand = next((r for r,c in ranks_in_hand.items() if c >=3), -1)
                            if trip_rank_in_hand != -1 and any(placed_row == 'top' and Card.get_rank_int(placed_card_val) == trip_rank_in_hand for placed_card_val, placed_row, _ in placements_list):
                                valid_action = False
                        if not valid_action: continue
                        deck_after_action = current_deck.copy()
                        for card_val, _, _ in placements_list: deck_after_action.discard(card_val)
                        if current_discard is not None: deck_after_action.discard(current_discard)
                        heuristic_score = MCTSNode._calculate_heuristic_score_v2(temp_board, deck_after_action)
                        candidate_actions.append({'score': heuristic_score, 'placements': placements_list, 'discarded': current_discard})
                    except ValueError: continue
        if not candidate_actions: return None
        candidate_actions.sort(key=lambda x: x['score'], reverse=True)
        return {'placements': candidate_actions[0]['placements'], 'discarded': candidate_actions[0]['discarded']}

def heuristic_rollout_simulation_v2(board_dict: Dict, deck_list: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    current_board = PlayerBoard()
    for r, cards_str_list in board_dict.get('rows', {}).items():
        for i, card_str_val in enumerate(cards_str_list):
            if card_str_val and card_str_val != CARD_PLACEHOLDER: # Corrected usage
                try: current_board.add_card(Card.from_str(card_str_val), r, i)
                except ValueError: pass
    
    # ИСПРАВЛЕНО: deck_list уже является list, создаем set для конструктора Deck
    deck_sim = Deck(cards=set(deck_list)) # Pass set to Deck constructor
    # ИСПРАВЛЕНО: Удаляем random.shuffle(deck_sim.cards), т.к. Deck.deal обеспечивает случайность
    
    simulation_actions_taken: List[Dict[str, Any]] = []
    try:
        while not current_board.is_complete():
            num_on_board = current_board.get_total_cards()
            if num_on_board == 0: num_to_deal = 5
            elif num_on_board < PlayerBoard.TOTAL_CAPACITY: num_to_deal = 3
            else: break
            if len(deck_sim) < num_to_deal: break # len(deck_sim) uses Deck.__len__
            
            dealt_cards = deck_sim.deal(num_to_deal)
            if not dealt_cards: break

            # ИСПРАВЛЕНО: Используем deck_sim.get_remaining_cards() и преобразуем в set
            deck_sim_set = set(deck_sim.get_remaining_cards())
            best_action = MCTSNode._choose_best_heuristic_placement_v2(current_board, dealt_cards, deck_sim_set)

            if best_action and best_action.get('placements'):
                action_placements = cast(List[Tuple[int, str, int]], best_action['placements'])
                # action_discarded = cast(Optional[int], best_action.get('discarded')) # Not directly used here
                valid_move = True
                for card_int, row, slot_idx in action_placements:
                    try: current_board.add_card(card_int, row, slot_idx)
                    except ValueError: valid_move = False; break
                if not valid_move: break
                simulation_actions_taken.append(best_action)
            else: break
        
        if check_board_foul(current_board): final_reward = -10.0
        else: final_reward = float(calculate_total_royalty_for_board(current_board))
    except Exception as e:
        logger.error(f"Error during heuristic rollout simulation: {e}", exc_info=True) # Removed version number from log
        final_reward = -20.0
    return final_reward, simulation_actions_taken

def run_parallel_rollout(board_dict: Dict, deck_list: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    return heuristic_rollout_simulation_v2(board_dict, deck_list)
