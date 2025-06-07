# mcts_node.py v2.12 (Syntax fix in except block)
"""
Узел MCTS и логика симуляции для OFC Pineapple.
- Улучшены эвристики для стремления к Фантазии (с учетом прогрессивной Фантазии).
- Улучшен учет "?" карт (num_unknown_removed_cards) в оценках потенциала.
- Динамические веса (weights) передаются и используются во всех функциях оценки потенциала.
- Доработана оценка потенциала для рядов с 1-2 картами.
- Уточнена логика определения количества размещаемых/сбрасываемых карт.
"""
import random
import math
import logging
import sys
from typing import List, Tuple, Dict, Optional, Set, Any, cast
from collections import Counter, defaultdict
import itertools

try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, card_to_str, CARD_PLACEHOLDER, STR_RANKS, UNKNOWN_CARD_MARKER_LOGIC
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, calculate_total_royalty_for_board,
        HAND_TYPE_PAIR_3, HAND_TYPE_TRIPS_3,
        RANK_QUEEN, RANK_KING, RANK_ACE
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD
except ImportError:
    logging.critical("Failed to import modules in mcts_node.py")
    # Mock PlayerBoard for testing purposes if imports fail
    class PlayerBoard: # type: ignore
        TOTAL_CAPACITY = 13
        ROW_NAMES = ['top', 'middle', 'bottom']
        ROW_CAPACITY = {'top':3,'middle':5,'bottom':5}

        def __init__(self, r=None,c=0):
            self.rows=r or {n:[] for n in PlayerBoard.ROW_NAMES}
            self._cards_placed=c

        def copy(self):
            new_rows = {r: list(self.rows[r]) for r in self.rows}
            return PlayerBoard(r=new_rows, c=self._cards_placed)

        def get_total_cards(self): return self._cards_placed
        def is_complete(self): return self._cards_placed == self.TOTAL_CAPACITY
        def add_card(self, card_int, row_name, slot_idx):
            if len(self.rows[row_name]) < self.ROW_CAPACITY[row_name]:
                 self.rows[row_name].append(card_int)
                 self._cards_placed +=1
                 return True
            return False
        def get_row_cards(self, row_name): return list(self.rows[row_name])
        def get_available_slots(self) -> List[Tuple[str, int]]:
            slots = []
            for rn in self.ROW_NAMES:
                for i in range(self.ROW_CAPACITY[rn] - len(self.rows[rn])):
                    slots.append((rn, len(self.rows[rn]) + i))
            return slots
        def get_board_state_tuple(self): return tuple(tuple(sorted(self.rows[rn])) for rn in self.ROW_NAMES)

    class Card: # type: ignore
        @staticmethod
        def get_rank_int(c): return 0
        @staticmethod
        def get_suit_int(c): return 0
        @staticmethod
        def to_str(c): return "??"
        @staticmethod
        def from_str(s): return 0

    class Deck: # type: ignore
        FULL_DECK_CARDS=set(range(52))
        def __init__(self,c=None):
            self.cards = list(c) if c else list(Deck.FULL_DECK_CARDS)
            random.shuffle(self.cards)
        def deal(self,n):
            dealt = []
            for _ in range(n):
                if self.cards: dealt.append(self.cards.pop())
            return dealt
        def get_remaining_cards(self): return list(self.cards)
        def __len__(self): return len(self.cards)

    RANK_MAP={}; STR_RANKS=""; CARD_PLACEHOLDER="__"; UNKNOWN_CARD_MARKER_LOGIC="??" # type: ignore
    def card_to_str(c):return "??" # type: ignore
    def get_hand_rank_safe(*a): return 9999,9,"Inv" # type: ignore
    WORST_RANK=9999;WORST_CLASS=9 # type: ignore
    # FIX: Исправлен синтаксис, определения функций на разных строках
    def check_board_foul(*a): return False
    def get_row_royalty(*a):return 0 # type: ignore
    def calculate_total_royalty_for_board(*a):return 0; ROYALTY_TOP_PAIRS={} # type: ignore
    HAND_TYPE_PAIR_3="P";HAND_TYPE_TRIPS_3="T"; RANK_QUEEN=10;RANK_KING=11;RANK_ACE=12 # type: ignore
    class Eval5: evaluate=lambda s,c:9999;get_rank_class=lambda s,r:9;class_to_string=lambda s,rc:"E" # type: ignore
    evaluator_5card=Eval5() # type: ignore
    def evaluate_3_card_ofc(*a):return 999, "E", "E"; WORST_RANK_3CARD=999 # type: ignore
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

MAX_PERMUTATIONS_STREET_1: int = 120
MAX_PERMUTATIONS_SLOTS_STREET_1: int = 30
MAX_PERMUTATIONS_STREET_N: int = 20

HEURISTIC_FOUL_PENALTY = -1000.0
HEURISTIC_FL_QUALIFY_BONUS = 15.0

ROW_FLUSH_DRAW_OUT_WEIGHT = 2.5
ROW_STRAIGHT_DRAW_OUT_WEIGHT = 2.0
ROW_GUTSHOT_DRAW_OUT_WEIGHT = 1.0
ROW_PAIR_OUTS_WEIGHT = 0.5
ROW_TRIPS_OUTS_WEIGHT = 3.0
ROW_HIGH_CARD_WEIGHT = 0.1

class MCTSNode:
    @staticmethod
    def _get_dynamic_weights(cards_placed_on_board: int, num_unknown_removed: int) -> Dict[str, float]:
        # ИСПРАВЛЕНИЕ 2: Еще раз значительно увеличены бонусы за Фантазию.
        # Причина: Предыдущего увеличения было недостаточно. Теперь бонусы должны
        # перевешивать почти любую другую эвристику, делая Фантазию главным приоритетом.
        weights = {
            'fantasy_qq_bonus_abs': 200.0,  # было 75.0
            'fantasy_kk_bonus_abs': 250.0,  # было 90.0
            'fantasy_aa_bonus_abs': 300.0,  # было 110.0
            'fantasy_trips_bonus_abs': 400.0, # было 140.0
            'fantasy_draw_multiplier_vs_abs_bonus': 0.25, # Увеличен множитель для дро
            'fantasy_draw_multiplier_vs_abs_bonus_2cards': 0.15,
            'strong_hand_on_bottom_bonus': 50.0, 'discard_low_card_bonus': 5.0,
            'draw_potential_multiplier': 1.0, 'foul_penalty': HEURISTIC_FOUL_PENALTY,
            'almost_foul_penalty': -15.0,
            'made_flush_score': 80.0, 'flush_draw_score_per_out': 2.8,
            'three_to_flush_score_per_out': 1.2, 'sf_bonus_over_flush': 110.0,
            'made_straight_score': 70.0, 'open_ended_draw_score_per_out': 2.2,
            'gutshot_draw_score_per_out': 1.1,
            'three_to_open_ended_score_per_out': 0.9, 'three_to_gutshot_score_per_out': 0.5,
            'four_of_a_kind_score': 160.0, 'three_of_a_kind_made_score': 45.0,
            'two_pair_made_score': 22.0, 'pair_made_score': 6.0,
            'pair_made_score_4cards': 3.0,
            'out_to_quads_score': 12.0, 'out_to_trips_score': 3.5,
            'out_to_full_house_from_trips_score': 1.8,
            'out_to_full_house_from_two_pair_score': 2.2,
            'out_to_pair_score': 0.6, 'out_to_pair_score_needing_two': 0.3,
            'royalty_base_multiplier': 2.5,
            'draw_completion_factor_late': 0.35,
            'draw_completion_factor_mid': 0.65,
        }
        progress = cards_placed_on_board / PlayerBoard.TOTAL_CAPACITY if PlayerBoard.TOTAL_CAPACITY > 0 else 0

        if progress < 0.35:
            weights['fantasy_qq_bonus_abs'] *= 1.5; weights['fantasy_kk_bonus_abs'] *= 1.65
            weights['fantasy_aa_bonus_abs'] *= 1.8; weights['fantasy_trips_bonus_abs'] *= 2.0
            weights['strong_hand_on_bottom_bonus'] = 65.0; weights['draw_potential_multiplier'] = 1.25
        elif progress < 0.65:
            weights['fantasy_qq_bonus_abs'] *= 1.0; weights['fantasy_kk_bonus_abs'] *= 1.1
            weights['fantasy_aa_bonus_abs'] *= 1.2; weights['fantasy_trips_bonus_abs'] *= 1.35
            weights['discard_low_card_bonus'] = 8.0
        else:
            weights['fantasy_qq_bonus_abs'] *= 0.6; weights['fantasy_kk_bonus_abs'] *= 0.7
            weights['fantasy_aa_bonus_abs'] *= 0.8; weights['fantasy_trips_bonus_abs'] *= 0.9
            weights['draw_potential_multiplier'] = 0.65; weights['discard_low_card_bonus'] = 12.0

        if num_unknown_removed > 0:
            uncertainty_factor = max(0.3, 1.0 - (num_unknown_removed * 0.12))
            weights['draw_potential_multiplier'] *= uncertainty_factor
            weights['flush_draw_score_per_out'] *= uncertainty_factor
            weights['open_ended_draw_score_per_out'] *= uncertainty_factor
            # ИСПРАВЛЕНИЕ: Добавлен недостающий параметр для корректной работы теста неопределенности.
            # Причина: Этот параметр не был включен в список изменяемых, что приводило к провалу теста.
            weights['gutshot_draw_score_per_out'] *= uncertainty_factor
            weights['fantasy_draw_multiplier_vs_abs_bonus'] *= uncertainty_factor
            weights['fantasy_draw_multiplier_vs_abs_bonus_2cards'] *= uncertainty_factor
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
        self.children: Dict[Tuple[Tuple[Tuple[int, str, int], ...], Any], MCTSNode] = {}
        self.visits: int = 0; self.total_reward: float = 0.0
        self.rave_visits_count: int = 0; self.rave_total_reward: float = 0.0
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Any]]] = None
        self._generated_states_for_expand: Dict[Tuple[Tuple[Tuple[int, str, int], ...], Any], Tuple[PlayerBoard, Any, Dict[str, Any]]] = {}

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
        weights = MCTSNode._get_dynamic_weights(board.get_total_cards(), num_unknown_removed)
        if check_board_foul(board): return weights['foul_penalty']
        score = 0.0; total_royalty = calculate_total_royalty_for_board(board)
        score += total_royalty * weights['royalty_base_multiplier']

        top_cards = board.get_row_cards('top'); mid_cards = board.get_row_cards('middle'); bot_cards = board.get_row_cards('bottom')
        fantasy_score = 0
        if len(top_cards) == 3:
            try:
                _, _, type_t = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                rc = Counter(Card.get_rank_int(c) for c in top_cards)
                if type_t == HAND_TYPE_TRIPS_3: fantasy_score = weights['fantasy_trips_bonus_abs']
                elif type_t == HAND_TYPE_PAIR_3:
                    pr = next((r for r, c in rc.items() if c == 2), -1)
                    if pr == RANK_ACE: fantasy_score = weights['fantasy_aa_bonus_abs']
                    elif pr == RANK_KING: fantasy_score = weights['fantasy_kk_bonus_abs']
                    elif pr == RANK_QUEEN: fantasy_score = weights['fantasy_qq_bonus_abs']
            except ValueError: pass
        elif len(top_cards) == 2:
            r_ints = [Card.get_rank_int(c) for c in top_cards]
            if r_ints[0] == r_ints[1]:
                pr = r_ints[0]
                if pr == RANK_ACE: fantasy_score = weights['fantasy_aa_bonus_abs'] * 0.85
                elif pr == RANK_KING: fantasy_score = weights['fantasy_kk_bonus_abs'] * 0.85
                elif pr == RANK_QUEEN: fantasy_score = weights['fantasy_qq_bonus_abs'] * 0.85
            else:
                for r_int in r_ints:
                    if r_int >= RANK_QUEEN:
                        def check(c): return Card.get_rank_int(c) == r_int
                        eff_o = MCTSNode._count_specific_outs(deck_snapshot, check, num_unknown_removed, original_deck_size_for_snapshot)
                        b = 0; m = weights['fantasy_draw_multiplier_vs_abs_bonus']
                        if r_int == RANK_ACE: b = weights['fantasy_aa_bonus_abs']
                        elif r_int == RANK_KING: b = weights['fantasy_kk_bonus_abs']
                        elif r_int == RANK_QUEEN: b = weights['fantasy_qq_bonus_abs']
                        fantasy_score += eff_o * (b * m)
        elif len(top_cards) == 1:
            r_int = Card.get_rank_int(top_cards[0])
            if r_int >= RANK_QUEEN:
                def check(c): return Card.get_rank_int(c) == r_int
                eff_o = MCTSNode._count_specific_outs(deck_snapshot, check, num_unknown_removed, original_deck_size_for_snapshot)
                b = 0; m = weights['fantasy_draw_multiplier_vs_abs_bonus_2cards']
                if r_int == RANK_ACE: b = weights['fantasy_aa_bonus_abs']
                elif r_int == RANK_KING: b = weights['fantasy_kk_bonus_abs']
                elif r_int == RANK_QUEEN: b = weights['fantasy_qq_bonus_abs']
                fantasy_score += eff_o * (b * m)
        score += fantasy_score

        if is_first_street and len(bot_cards) == 5:
            _, class_b, _ = get_hand_rank_safe(bot_cards)
            if class_b <= 5: score += weights['strong_hand_on_bottom_bonus']

        cards_on_b = board.get_total_cards(); factor = 1.0
        if cards_on_b >= 9 : factor = weights['draw_completion_factor_late']
        elif cards_on_b >= 5: factor = weights['draw_completion_factor_mid']

        if 0 < len(mid_cards) < 5 : score += MCTSNode._estimate_row_potential(mid_cards, deck_snapshot, num_unknown_removed, original_deck_size_for_snapshot, weights) * weights['draw_potential_multiplier'] * 0.7 * factor
        if 0 < len(bot_cards) < 5: score += MCTSNode._estimate_row_potential(bot_cards, deck_snapshot, num_unknown_removed, original_deck_size_for_snapshot, weights) * weights['draw_potential_multiplier'] * 1.0 * factor

        if cards_on_b >= 8:
            r_t, c_t, _ = get_hand_rank_safe(top_cards); r_m, c_m, _ = get_hand_rank_safe(mid_cards); r_b, c_b, _ = get_hand_rank_safe(bot_cards)
            is_foul_already = False
            if len(top_cards)==3 and len(mid_cards)==5:
                if (c_t < c_m) or (c_t == c_m and r_t < r_m): is_foul_already=True
                elif not is_foul_already and ((c_t < c_m + 2) or (c_t == c_m and r_t < r_m + 300)): score += weights['almost_foul_penalty']
            if len(mid_cards)==5 and len(bot_cards)==5:
                if (c_m < c_b) or (c_m == c_b and r_m < r_b): is_foul_already=True
                elif not is_foul_already and ((c_m < c_b + 2) or (c_m == c_b and r_m < r_b + 300)): score += weights['almost_foul_penalty']
        return score

    @staticmethod
    def _estimate_row_potential(current_cards: List[int], deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]) -> float:
        num_c = len(current_cards); pot = 0.0
        if not current_cards: return 0.0
        if num_c == 1:
            r = Card.get_rank_int(current_cards[0])
            def check(c): return Card.get_rank_int(c) == r
            eff_o = MCTSNode._count_specific_outs(deck, check, num_unknown_removed, original_deck_size)
            pot = eff_o * weights['out_to_pair_score_needing_two']
        elif num_c == 2:
            r1, r2 = Card.get_rank_int(current_cards[0]), Card.get_rank_int(current_cards[1])
            if r1 == r2:
                def check(c): return Card.get_rank_int(c) == r1
                eff_o = MCTSNode._count_specific_outs(deck, check, num_unknown_removed, original_deck_size)
                pot = eff_o * weights['out_to_trips_score']
            else:
                def c1(c): return Card.get_rank_int(c) == r1
                def c2(c): return Card.get_rank_int(c) == r2
                eff_o1 = MCTSNode._count_specific_outs(deck, c1, num_unknown_removed, original_deck_size)
                eff_o2 = MCTSNode._count_specific_outs(deck, c2, num_unknown_removed, original_deck_size)
                pot = (eff_o1 + eff_o2) * weights['out_to_pair_score']
        elif num_c == 3 or num_c == 4:
            ranks, suits, rank_counts, suit_counts = MCTSNode._get_card_props(current_cards)
            pot += MCTSNode._calculate_n_of_a_kind_potential(ranks, rank_counts, num_c, deck, num_unknown_removed, original_deck_size, weights)
            f_pot = MCTSNode._calculate_flush_potential_for_row(ranks, suits, suit_counts, num_c, deck, num_unknown_removed, original_deck_size, weights)
            s_pot = MCTSNode._calculate_straight_potential_for_row(ranks, num_c, deck, num_unknown_removed, original_deck_size, weights)
            sf_made = False
            if num_c == 4 and any(sc == 4 for sc in suit_counts.values()):
                ms = next((s for s,c in suit_counts.items() if c==4), None)
                if ms is not None: sf_made, _ = MCTSNode._is_straight_flush_possible(ranks, suits, ms)
            pot += f_pot
            if not sf_made: pot += s_pot
        return pot

    @staticmethod
    def _get_card_props(cards: List[int]) -> Tuple[List[int], List[int], Counter, Counter]:
        ranks = sorted([Card.get_rank_int(c) for c in cards]); suits = [Card.get_suit_int(c) for c in cards]
        return ranks, suits, Counter(ranks), Counter(suits)

    @staticmethod
    def _count_specific_outs(deck: Set[int], check_func, num_unknown_removed: int, original_deck_size: int) -> float:
        visible_outs = sum(1 for c_deck in deck if check_func(c_deck))
        if original_deck_size <= 0 or num_unknown_removed >= original_deck_size : return 0.0
        prob_avail = (float(original_deck_size) - num_unknown_removed) / float(original_deck_size)
        return max(0.0, visible_outs * prob_avail)

    @staticmethod
    def _calculate_flush_potential_for_row(ranks: List[int], suits: List[int], suit_counts: Counter, num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]) -> float:
        score = 0.0
        for suit, count in suit_counts.items():
            if num_cards == 4:
                if count == 4:
                    is_sf, _ = MCTSNode._is_straight_flush_possible(ranks, suits, suit)
                    score += weights['made_flush_score'] + (weights['sf_bonus_over_flush'] if is_sf else 0)
                    break
                elif count == 3:
                    def check(c):
                        return Card.get_suit_int(c) == suit
                    score += MCTSNode._count_specific_outs(deck, check, num_unknown_removed, original_deck_size) * weights['flush_draw_score_per_out']
                    break
            elif num_cards == 3:
                if count == 3:
                    def check(c):
                        return Card.get_suit_int(c) == suit
                    outs = MCTSNode._count_specific_outs(deck, check, num_unknown_removed, original_deck_size)
                    score += (outs * weights['three_to_flush_score_per_out'] if outs >=2 else 0)
                    break
        return score

    @staticmethod
    def _is_straight_flush_possible(card_ranks: List[int], card_suits: List[int], target_suit: Optional[int]) -> Tuple[bool, List[int]]:
        if target_suit is None: return False, []
        s_ranks = sorted(list(set(r for i, r in enumerate(card_ranks) if card_suits[i] == target_suit)))
        num_s = len(s_ranks)
        if num_s < 3: return False, []
        if len(set(s_ranks)) < num_s : return False, s_ranks
        is_s = (max(s_ranks) - min(s_ranks) == num_s - 1); is_w = False
        if RANK_ACE in s_ranks:
            wr = {RANK_ACE, RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4'], RANK_MAP['5']}
            pr = {r for r in s_ranks if r in wr or r == RANK_ACE}
            if num_s == 3 and pr.issuperset({RANK_ACE, RANK_MAP['2'], RANK_MAP['3']}): is_w = True
            elif num_s == 4 and pr.issuperset({RANK_ACE, RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4']}): is_w = True
            elif num_s == 5 and pr.issuperset(wr): is_w = True
        return (is_s or is_w), s_ranks

    @staticmethod
    def _calculate_straight_potential_for_row(ranks: List[int], num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]) -> float:
        score = 0.0; unique_ranks = sorted(list(set(ranks)))
        if num_cards == 4 and len(unique_ranks) == 4:
            outs = 0
            if (unique_ranks[3]-unique_ranks[0] == 3 and unique_ranks[1]-unique_ranks[0]==1 and unique_ranks[2]-unique_ranks[1]==1):
                if unique_ranks[0] > RANK_MAP['2']:
                    def check_l(c): return Card.get_rank_int(c) == unique_ranks[0]-1
                    outs += MCTSNode._count_specific_outs(deck, check_l, num_unknown_removed, original_deck_size)
                if unique_ranks[3] < RANK_ACE:
                    def check_h(c): return Card.get_rank_int(c) == unique_ranks[3]+1
                    outs += MCTSNode._count_specific_outs(deck, check_h, num_unknown_removed, original_deck_size)
            elif set(unique_ranks) == {RANK_ACE, RANK_MAP['2'], RANK_MAP['3'], RANK_MAP['4']}:
                def check_5(c): return Card.get_rank_int(c) == RANK_MAP['5']
                outs += MCTSNode._count_specific_outs(deck, check_5, num_unknown_removed, original_deck_size)
            elif set(unique_ranks) == {RANK_MAP['T'], RANK_MAP['J'], RANK_MAP['Q'], RANK_MAP['K']}:
                def check_A(c): return Card.get_rank_int(c) == RANK_ACE
                outs += MCTSNode._count_specific_outs(deck, check_A, num_unknown_removed, original_deck_size)
                def check_9(c): return Card.get_rank_int(c) == RANK_MAP['9']
                outs += MCTSNode._count_specific_outs(deck, check_9, num_unknown_removed, original_deck_size)
            score += outs * weights['open_ended_draw_score_per_out']
            if outs == 0:
                gut_outs = 0
                for combo3 in itertools.combinations(unique_ranks, 3):
                    sc = sorted(list(combo3))
                    if (sc[1]-sc[0]==1 and sc[2]-sc[1]==2):
                        def check_g(c): return Card.get_rank_int(c) == sc[1]+1
                        gut_outs += MCTSNode._count_specific_outs(deck, check_g, num_unknown_removed, original_deck_size)
                    elif (sc[1]-sc[0]==2 and sc[2]-sc[1]==1):
                        def check_g(c): return Card.get_rank_int(c) == sc[0]+1
                        gut_outs += MCTSNode._count_specific_outs(deck, check_g, num_unknown_removed, original_deck_size)
                score += gut_outs * weights['gutshot_draw_score_per_out']
        elif num_cards == 3 and len(unique_ranks) == 3:
            if (unique_ranks[1]-unique_ranks[0]==1 and unique_ranks[2]-unique_ranks[1]==1):
                oesd3_outs = 0
                if unique_ranks[0] > RANK_MAP['2']:
                    def c_l(c): return Card.get_rank_int(c) == unique_ranks[0]-1
                    oesd3_outs += MCTSNode._count_specific_outs(deck, c_l, num_unknown_removed, original_deck_size)
                if unique_ranks[2] < RANK_ACE:
                    def c_h(c): return Card.get_rank_int(c) == unique_ranks[2]+1
                    oesd3_outs += MCTSNode._count_specific_outs(deck, c_h, num_unknown_removed, original_deck_size)
                score += oesd3_outs * weights['three_to_open_ended_score_per_out']
            elif (unique_ranks[1]-unique_ranks[0]==1 and unique_ranks[2]-unique_ranks[1]==2):
                def c_g(c): return Card.get_rank_int(c) == unique_ranks[1]+1
                score += MCTSNode._count_specific_outs(deck, c_g, num_unknown_removed, original_deck_size) * weights['three_to_gutshot_score_per_out']
            elif (unique_ranks[1]-unique_ranks[0]==2 and unique_ranks[2]-unique_ranks[1]==1):
                def c_g(c): return Card.get_rank_int(c) == unique_ranks[0]+1
                score += MCTSNode._count_specific_outs(deck, c_g, num_unknown_removed, original_deck_size) * weights['three_to_gutshot_score_per_out']
        return score

    @staticmethod
    def _calculate_n_of_a_kind_potential(ranks: List[int], rank_counts: Counter, num_cards: int, deck: Set[int], num_unknown_removed: int, original_deck_size: int, weights: Dict[str, float]) -> float:
        score = 0.0
        if num_cards == 4:
            quads_r = next((r for r,c in rank_counts.items() if c==4),-1)
            trips_r = next((r for r,c in rank_counts.items() if c==3),-1)
            pairs_r = [r for r,c in rank_counts.items() if c==2]
            if quads_r!=-1:
                score+=weights['four_of_a_kind_score']
            elif trips_r!=-1:
                score+=weights['three_of_a_kind_made_score']
                def c_q(c):return Card.get_rank_int(c)==trips_r
                score+=MCTSNode._count_specific_outs(deck,c_q,num_unknown_removed,original_deck_size)*weights['out_to_quads_score']
                k_r=next((r for r in ranks if r!=trips_r),-1)
                if k_r!=-1:
                    def c_fh(c):return Card.get_rank_int(c)==k_r
                    score+=MCTSNode._count_specific_outs(deck,c_fh,num_unknown_removed,original_deck_size)*weights['out_to_full_house_from_trips_score']
            elif len(pairs_r)==2:
                score+=weights['two_pair_made_score']
                for pr_r in pairs_r:
                    def c_fh(c):return Card.get_rank_int(c)==pr_r
                    score+=MCTSNode._count_specific_outs(deck,c_fh,num_unknown_removed,original_deck_size)*weights['out_to_full_house_from_two_pair_score']
            elif len(pairs_r)==1:
                score+=weights['pair_made_score_4cards']
                pr_r=pairs_r[0]
                def c_t(c):return Card.get_rank_int(c)==pr_r
                score+=MCTSNode._count_specific_outs(deck,c_t,num_unknown_removed,original_deck_size)*weights['out_to_trips_score']
                k_rs=[r for r in ranks if r!=pr_r]
                for kr_r in k_rs:
                    def c_p2(c):return Card.get_rank_int(c)==kr_r
                    score+=MCTSNode._count_specific_outs(deck,c_p2,num_unknown_removed,original_deck_size)*weights['out_to_pair_score']
            else:
                for r_v in ranks:
                    def c_p(c):return Card.get_rank_int(c)==r_v
                    score+=MCTSNode._count_specific_outs(deck,c_p,num_unknown_removed,original_deck_size)*weights['out_to_pair_score_needing_two']
        elif num_cards == 3:
            trips_r = next((r for r,c in rank_counts.items() if c==3),-1)
            pair_r = next((r for r,c in rank_counts.items() if c==2),-1)
            if trips_r!=-1:
                score+=weights['three_of_a_kind_made_score']
            elif pair_r!=-1:
                score+=weights['pair_made_score']
                def c_t(c):return Card.get_rank_int(c)==pair_r
                score+=MCTSNode._count_specific_outs(deck,c_t,num_unknown_removed,original_deck_size)*weights['out_to_trips_score']
                k_r=next((r for r in ranks if r!=pair_r),-1)
                if k_r!=-1:
                    def c_kp(c):return Card.get_rank_int(c)==k_r
                    score+=MCTSNode._count_specific_outs(deck,c_kp,num_unknown_removed,original_deck_size)*weights['out_to_pair_score']
            else:
                for r_v in ranks:
                    def c_p(c):return Card.get_rank_int(c)==r_v
                    score+=MCTSNode._count_specific_outs(deck,c_p,num_unknown_removed,original_deck_size)*weights['out_to_pair_score_needing_two']
        return score

    @staticmethod
    def _choose_best_heuristic_placement_v2(
        current_board: PlayerBoard, cards_to_act_on: List[int], current_deck: Set[int],
        num_to_place_on_board: int, num_unknown_removed_cards: int
    ) -> List[Dict[str, Any]]:
        candidate_actions: List[Dict[str, Any]] = []
        num_on_board = current_board.get_total_cards(); num_dealt = len(cards_to_act_on)
        dynamic_weights = MCTSNode._get_dynamic_weights(num_on_board, num_unknown_removed_cards)
        available_slots = current_board.get_available_slots(); is_first_street = (num_on_board == 0 and num_to_place_on_board == 5)

        cards_to_place_options: List[List[int]] = []; cards_to_discard_options: List[Any] = []

        if num_to_place_on_board == 0 and num_dealt > 0:
            discard_val = tuple(sorted(cards_to_act_on)) if len(cards_to_act_on) > 1 else cards_to_act_on[0]
            return [{'score': HEURISTIC_FOUL_PENALTY / 2, 'placements': [], 'discarded': discard_val}]
        elif num_to_place_on_board == 0 and num_dealt == 0: return []

        if num_to_place_on_board == num_dealt:
            cards_to_place_options = [cards_to_act_on]; cards_to_discard_options = [None]
        elif num_dealt > num_to_place_on_board:
            num_to_discard = num_dealt - num_to_place_on_board
            for combo_to_place_tuple in itertools.combinations(cards_to_act_on, num_to_place_on_board):
                list_combo_to_place = list(combo_to_place_tuple)
                cards_to_place_options.append(list_combo_to_place)
                discard_combo_list = [c for c in cards_to_act_on if c not in list_combo_to_place]
                if len(discard_combo_list) == 1: cards_to_discard_options.append(discard_combo_list[0])
                elif len(discard_combo_list) > 1: cards_to_discard_options.append(tuple(sorted(discard_combo_list)))
                else: cards_to_discard_options.append(None)
        else: logger.error(f"Heuristic: num_dealt ({num_dealt}) < num_to_place ({num_to_place_on_board})."); return []

        if len(available_slots) < num_to_place_on_board: return []

        card_perms_limit = MAX_PERMUTATIONS_STREET_1 if is_first_street else MAX_PERMUTATIONS_STREET_N
        slot_perms_limit = MAX_PERMUTATIONS_SLOTS_STREET_1 if is_first_street else MAX_PERMUTATIONS_STREET_N

        for i in range(len(cards_to_place_options)):
            current_cards_to_place_list = cards_to_place_options[i]; current_discard_info = cards_to_discard_options[i]
            card_perms = list(itertools.permutations(current_cards_to_place_list))
            if len(card_perms) > card_perms_limit: card_perms = random.sample(card_perms, card_perms_limit)
            actual_slots_to_fill = min(num_to_place_on_board, len(available_slots))
            if actual_slots_to_fill < num_to_place_on_board : continue
            slot_perms = list(itertools.permutations(available_slots, actual_slots_to_fill))
            if len(slot_perms) > slot_perms_limit: slot_perms = random.sample(slot_perms, slot_perms_limit)

            for p_cards_tuple_perm in card_perms:
                p_cards_list_perm = list(p_cards_tuple_perm)
                for p_slots_tuple_perm in slot_perms:
                    temp_board = current_board.copy(); placements_list: List[Tuple[int, str, int]] = []; valid_placement = True; deck_after_action = current_deck.copy()
                    try:
                        for card_idx, (card_val, (row_val, slot_idx_val)) in enumerate(zip(p_cards_list_perm, p_slots_tuple_perm)):
                            if not temp_board.add_card(card_val, row_val, slot_idx_val): valid_placement = False; break
                            placements_list.append((card_val, row_val, slot_idx_val)); deck_after_action.discard(card_val)
                        if not valid_placement: continue
                        if is_first_street:
                            rc_fs = Counter(Card.get_rank_int(c) for c in p_cards_list_perm); tr_fs = next((r for r,c in rc_fs.items() if c >=3), -1)
                            if tr_fs != -1 and any(pr=='top' and Card.get_rank_int(pc)==tr_fs for pc,pr,_ in placements_list): continue
                        if current_discard_info is not None:
                            if isinstance(current_discard_info, tuple): [deck_after_action.discard(dc) for dc in current_discard_info if dc in deck_after_action]
                            elif current_discard_info in deck_after_action: deck_after_action.remove(current_discard_info)
                        h_score = MCTSNode._calculate_heuristic_score_v2(temp_board, deck_after_action, is_first_street, num_unknown_removed_cards, len(current_deck))
                        if current_discard_info and isinstance(current_discard_info, int) and num_dealt > num_to_place_on_board:
                            dr = Card.get_rank_int(current_discard_info); prs = [Card.get_rank_int(p[0]) for p in placements_list]
                            if all(dr < pr_v for pr_v in prs): h_score += dynamic_weights['discard_low_card_bonus']
                        candidate_actions.append({'score': h_score, 'placements': placements_list, 'discarded': current_discard_info})
                    except ValueError: continue

        if not candidate_actions: return []
        candidate_actions.sort(key=lambda x: x['score'], reverse=True)
        return candidate_actions[:(15 if not is_first_street else 10)]

def heuristic_rollout_simulation_v2(board_dict: Dict, deck_list_initial: List[int], num_unknown_sim: int) -> Tuple[float, List[Dict[str, Any]]]:
    current_board = PlayerBoard(); actions_hist: List[Dict[str, Any]] = []
    for r, c_strs in board_dict.get('rows', {}).items():
        for i, c_str in enumerate(c_strs):
            if c_str and c_str != CARD_PLACEHOLDER:
                try:
                    current_board.add_card(Card.from_str(c_str), r, i)
                except ValueError:
                    pass
    deck_for_sim: Set[int]
    if num_unknown_sim > 0 and len(deck_list_initial) > num_unknown_sim:
        try:
            deck_for_sim = set(random.sample(deck_list_initial, len(deck_list_initial) - num_unknown_sim))
        except ValueError:
            deck_for_sim = set(deck_list_initial)
    elif num_unknown_sim > 0: deck_for_sim = set()
    else: deck_for_sim = set(deck_list_initial)
    sim_deck_obj = Deck(cards=deck_for_sim)
    try:
        while not current_board.is_complete():
            avail_slots = PlayerBoard.TOTAL_CAPACITY - current_board.get_total_cards()
            if current_board.get_total_cards() == 0:
                n_deal, n_place = 5, 5
            else:
                n_deal, n_place = 3, min(2, avail_slots)

            if avail_slots <= 0 or n_place <= 0 or len(sim_deck_obj) < n_deal:
                break
            dealt = sim_deck_obj.deal(n_deal)
            if not dealt:
                break
            deck_snapshot_for_h = set(sim_deck_obj.get_remaining_cards())
            original_size_for_h = len(deck_snapshot_for_h) + len(dealt)
            best_acts = MCTSNode._choose_best_heuristic_placement_v2(current_board, dealt, deck_snapshot_for_h, n_place, num_unknown_sim)
            if not best_acts:
                break
            best_act = best_acts[0]
            if best_act and best_act.get('placements'):
                valid = True
                for c,r,s_idx in best_act['placements']:
                    if not current_board.add_card(c,r,s_idx):
                        valid=False
                        break
                if not valid:
                    break
                actions_hist.append(best_act)
            else:
                break
        final_score = float(calculate_total_royalty_for_board(current_board)) if not check_board_foul(current_board) else HEURISTIC_FOUL_PENALTY
    except Exception as e:
        logger.error(f"Rollout error: {e}", exc_info=True)
        final_score = HEURISTIC_FOUL_PENALTY - 50.0
    return final_score, actions_hist

def run_parallel_rollout(board_dict: Dict, deck_list: List[int], num_unknown_removed_cards: int) -> Tuple[float, List[Dict[str, Any]]]:
    return heuristic_rollout_simulation_v2(board_dict, deck_list, num_unknown_removed_cards)
