# mcts_node.py v2.15 (Advanced Potential Calculation based on Outs and EV)
"""
Узел MCTS и логика симуляции для OFC Pineapple.
- ПОЛНОСТЬЮ ПЕРЕРАБОТАНА ЭВРИСТИКА: Вместо статических весов потенциала
  внедрена новая функция _estimate_row_potential_v2, которая:
    - Считает реальные ауты в оставшейся колоде для улучшения руки.
    - Оценивает ожидаемую ценность (Expected Value) от роялти.
    - Учитывает вероятность прихода нужных карт.
  Это позволяет ИИ принимать гораздо более осмысленные и стратегически верные риски.
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
    def check_board_foul(*a): return False
    def get_row_royalty(*a):return 0 # type: ignore
    def calculate_total_royalty_for_board(*a):return 0; ROYALTY_TOP_PAIRS={}; ROYALTY_MIDDLE_POINTS={}; ROYALTY_BOTTOM_POINTS={} # type: ignore
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

MAX_PERMUTATIONS_STREET_1: int = 999999
MAX_PERMUTATIONS_STREET_N: int = 999999

MAX_PERMUTATIONS_SLOTS_STREET_1: int = 5040
MAX_PERMUTATIONS_SLOTS_STREET_N: int = 120

HEURISTIC_FOUL_PENALTY = -1000.0
SIMULATION_FOUL_PENALTY = -2000.0
FANTASY_QUALIFY_BONUS = 300.0
ROYALTY_MULTIPLIER = 1.0 # Множитель для EV, не для готовых роялти
MADE_HAND_BONUS = 100.0
FIRST_STREET_STRONG_HAND_BOTTOM_BONUS = 50.0

class MCTSNode:
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
        if len(top_cards) == PlayerBoard.ROW_CAPACITY['top'] and len(mid_cards) > 0 and mid_class != WORST_CLASS:
            if (top_class < mid_class) or (top_class == mid_class and top_rank < mid_rank):
                foul_risk_penalty += HEURISTIC_FOUL_PENALTY / 2
        if len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle'] and len(bot_cards) > 0 and bot_class != WORST_CLASS:
            if (mid_class < bot_class) or (mid_class == bot_class and mid_rank < bot_rank):
                foul_risk_penalty += HEURISTIC_FOUL_PENALTY / 2
        score += foul_risk_penalty

        is_fantasy_qualified = False
        if len(top_cards) == 3:
            royalty = get_row_royalty(top_cards, 'top')
            if royalty > 0:
                score += royalty
                if royalty >= 10:
                    is_fantasy_qualified = True
        if len(mid_cards) == 5:
            score += get_row_royalty(mid_cards, 'middle')
        if len(bot_cards) == 5:
            score += get_row_royalty(bot_cards, 'bottom')

        if is_fantasy_qualified:
            score += FANTASY_QUALIFY_BONUS

        # >>> НАЧАЛО ИЗМЕНЕНИЯ: ВЫЗОВ НОВОЙ ФУНКЦИИ ОЦЕНКИ ПОТЕНЦИАЛА <<<
        cards_on_board = board.get_total_cards()
        score += MCTSNode._estimate_row_potential_v2(top_cards, 'top', deck_snapshot, cards_on_board)
        score += MCTSNode._estimate_row_potential_v2(mid_cards, 'middle', deck_snapshot, cards_on_board)
        score += MCTSNode._estimate_row_potential_v2(bot_cards, 'bottom', deck_snapshot, cards_on_board)
        # >>> КОНЕЦ ИЗМЕНЕНИЯ <<<

        if is_first_street:
            # Check for strong hand in bottom row on first street
            # bot_cards variable is already defined and populated earlier in this function
            if len(bot_cards) == PlayerBoard.ROW_CAPACITY['bottom']: # Should be 5 for first street bottom placement
                # bot_rank, bot_class are also already defined and populated
                if bot_class <= 5 and bot_rank != WORST_RANK: # Straight or better
                    score += FIRST_STREET_STRONG_HAND_BOTTOM_BONUS

        return score

    @staticmethod
    def _estimate_row_potential_v2(cards: List[int], row_name: str, deck: Set[int], cards_on_board: int) -> float:
        """
        Оценивает потенциал ряда на основе аутов и ожидаемого роялти (EV).
        """
        n = len(cards)
        capacity = PlayerBoard.ROW_CAPACITY.get(row_name, 0)
        if n == 0 or n == capacity:
            return 0.0

        potential = 0.0
        ranks, suits, rank_counts, suit_counts = MCTSNode._get_card_props(cards)
        
        # Вероятностный множитель: чем меньше карт осталось добрать, тем выше шанс
        cards_to_draw = PlayerBoard.TOTAL_CAPACITY - cards_on_board
        if cards_to_draw <= 0: return 0.0
        
        # --- Потенциал для 5-карточных рядов (middle, bottom) ---
        if capacity == 5:
            # Потенциал на Флеш
            for suit, count in suit_counts.items():
                if count == 4: # 4 карты к флешу
                    outs = sum(1 for c in deck if Card.get_suit_int(c) == suit)
                    royalty = ROYALTY_MIDDLE_POINTS.get("Flush", 0) if row_name == 'middle' else ROYALTY_BOTTOM_POINTS.get("Flush", 4)
                    potential += (outs / len(deck) if deck else 0) * royalty * ROYALTY_MULTIPLIER
            
            # Потенциал на Фулл-Хаус
            if 3 in rank_counts.values() and 2 in rank_counts.values(): # Уже фулл-хаус
                 pass # Уже оценено как готовая рука
            elif list(rank_counts.values()).count(2) == 2: # Две пары -> ауты на фулл-хаус
                pair_ranks = [r for r, c in rank_counts.items() if c == 2]
                outs = sum(1 for c in deck if Card.get_rank_int(c) in pair_ranks)
                royalty = ROYALTY_MIDDLE_POINTS.get("Full House", 0) if row_name == 'middle' else ROYALTY_BOTTOM_POINTS.get("Full House", 6)
                potential += (outs / len(deck) if deck else 0) * royalty * ROYALTY_MULTIPLIER
            elif 3 in rank_counts.values(): # Сет -> ауты на фулл-хаус
                outs = sum(1 for c in deck if Card.get_rank_int(c) not in ranks) # Любая карта другого ранга для пары
                royalty = ROYALTY_MIDDLE_POINTS.get("Full House", 0) if row_name == 'middle' else ROYALTY_BOTTOM_POINTS.get("Full House", 6)
                potential += (outs / len(deck) if deck else 0) * royalty * ROYALTY_MULTIPLIER * 0.5 # Понижающий коэфф.

        # --- Потенциал для 3-карточного ряда (top) ---
        if capacity == 3 and n == 2:
            # Потенциал на пару -> трипс
            if ranks[0] == ranks[1]:
                pair_rank = ranks[0]
                outs = sum(1 for c in deck if Card.get_rank_int(c) == pair_rank)
                # Роялти за трипс на топе зависит от ранга
                trip_royalty = get_row_royalty([cards[0], cards[1], next(c for c in deck if Card.get_rank_int(c) == pair_rank)], 'top') if outs > 0 else 0
                potential += (outs / len(deck) if deck else 0) * trip_royalty * ROYALTY_MULTIPLIER
            # Потенциал на пару (для Фантазии)
            else:
                for r in ranks:
                    if r >= RANK_QUEEN:
                        outs = sum(1 for c in deck if Card.get_rank_int(c) == r)
                        pair_royalty = get_row_royalty([cards[0], cards[1], next(c for c in deck if Card.get_rank_int(c) == r)], 'top') if outs > 0 else 0
                        if pair_royalty > 0: # Дает роялти (QQ+)
                            potential += (outs / len(deck) if deck else 0) * pair_royalty * ROYALTY_MULTIPLIER

        return potential

    @staticmethod
    def _choose_best_heuristic_placement_v2(
        current_board: PlayerBoard, cards_to_act_on: List[int], current_deck: Set[int],
        num_to_place_on_board: int, num_unknown_removed_cards: int
    ) -> List[Dict[str, Any]]:
        candidate_actions: List[Dict[str, Any]] = []
        num_on_board = current_board.get_total_cards(); num_dealt = len(cards_to_act_on)
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
                            discarded_rank = Card.get_rank_int(current_discard_info)
                            h_score += (12 - discarded_rank) * 1.0

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

        # Score determination at the end of simulation
        if current_board.is_complete():
            if check_board_foul(current_board):
                final_score = SIMULATION_FOUL_PENALTY
            else: # Complete and not a foul
                final_score = float(calculate_total_royalty_for_board(current_board))
        else: # Board is NOT complete (simulation ended prematurely)
            is_foul_incomplete = False
            top_cards = current_board.get_row_cards('top')
            mid_cards = current_board.get_row_cards('middle')
            bot_cards = current_board.get_row_cards('bottom')

            if len(top_cards) == PlayerBoard.ROW_CAPACITY['top'] and \
               len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle']:
                top_rank, top_class, _ = get_hand_rank_safe(top_cards)
                mid_rank, mid_class, _ = get_hand_rank_safe(mid_cards)
                if mid_class != WORST_CLASS: # Only check if middle is a valid hand
                    if (top_class < mid_class) or (top_class == mid_class and top_rank < mid_rank):
                        is_foul_incomplete = True

            if not is_foul_incomplete and \
               len(mid_cards) == PlayerBoard.ROW_CAPACITY['middle'] and \
               len(bot_cards) == PlayerBoard.ROW_CAPACITY['bottom']:
                mid_rank, mid_class, _ = get_hand_rank_safe(mid_cards)
                bot_rank, bot_class, _ = get_hand_rank_safe(bot_cards)
                if bot_class != WORST_CLASS: # Only check if bottom is a valid hand
                    if (mid_class < bot_class) or (mid_class == mid_class and mid_rank < bot_rank):
                        is_foul_incomplete = True

            if is_foul_incomplete:
                final_score = HEURISTIC_FOUL_PENALTY
            else:
                final_score = float(calculate_total_royalty_for_board(current_board))

    except Exception as e:
        logger.error(f"Rollout error: {e}", exc_info=True)
        final_score = SIMULATION_FOUL_PENALTY - 50.0 # Use the new harsher penalty for exceptions
    return final_score, actions_hist

def run_parallel_rollout(board_dict: Dict, deck_list: List[int], num_unknown_removed_cards: int) -> Tuple[float, List[Dict[str, Any]]]:
    return heuristic_rollout_simulation_v2(board_dict, deck_list, num_unknown_removed_cards)
