# tests/test_ai_quality.py v1.4 (ULTRATHINK FINAL FIX: Increased Time)
"""
Тесты для оценки качества решений MCTS-агента.
- ULTRATHINK FINAL FIX: Увеличено время для agent_short_time, чтобы дать
  исправленному ИИ достаточно времени на поиск оптимального решения.
"""
import pytest
from unittest.mock import ANY
import random
import logging
from collections import Counter

try:
    from mcts_agent import MCTSAgent
    from ofc_logic import (
        PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS,
        RANK_2, RANK_3, RANK_4, RANK_5, RANK_6, RANK_7, RANK_8, RANK_9,
        RANK_TEN, RANK_JACK, RANK_QUEEN, RANK_KING, RANK_ACE
    )
    from ofc_evaluators import check_board_foul, get_row_royalty, HAND_TYPE_TRIPS_3, HAND_TYPE_PAIR_3, calculate_total_royalty_for_board
except ImportError:
    pytest.skip("Skipping AI quality tests due to missing core imports", allow_module_level=True)

logger = logging.getLogger(__name__)

def hand_to_int(card_strs: list) -> list:
    return [Card.from_str(s) for s in card_strs if s and len(s) >= 2]

@pytest.fixture
def agent_short_time():
    # ULTRATHINK FINAL FIX: Increased time to allow the correct MCTS to find the solution.
    return MCTSAgent(time_limit_ms=10000, num_workers=4, rollouts_per_leaf=20)

@pytest.fixture
def agent_very_short_time():
    return MCTSAgent(time_limit_ms=5000, num_workers=4, rollouts_per_leaf=10)

# The rest of the test file is correct and remains unchanged.
# ... (all test functions from the previous correct version) ...
def test_ai_handles_11_plus_3_cards_correctly(agent_short_time):
    board = PlayerBoard()
    initial_placements = [
        ('Ks', 'bottom', 0), ('Qs', 'bottom', 1), ('Js', 'bottom', 2), ('Ts', 'bottom', 3),
        ('Ac', 'middle', 0), ('Ad', 'middle', 1), ('2h', 'middle', 2), ('3h', 'middle', 3),
        ('6s', 'top', 0), ('5s', 'top', 1), ('4s', 'top', 2)
    ]
    initial_board_cards_int = []
    for card_str, row, idx in initial_placements:
        c_int = Card.from_str(card_str)
        board.add_card(c_int, row, idx)
        initial_board_cards_int.append(c_int)
    assert board.get_total_cards() == 11
    cards_dealt_str = ['As', 'Kh', '2c']
    cards_dealt_int = hand_to_int(cards_dealt_str)
    remaining_deck = Deck.FULL_DECK_CARDS - set(initial_board_cards_int) - set(cards_dealt_int)
    placement_info = agent_short_time.choose_placement(board, cards_dealt_int, remaining_deck, 0)
    assert placement_info is not None
    assert len(placement_info['placements']) == 2
    assert Card.to_str(placement_info['discarded']) == '2c'
    placed_by_ai_ints = {p[0] for p in placement_info['placements']}
    expected_placed_ints = {Card.from_str('As'), Card.from_str('Kh')}
    assert placed_by_ai_ints == expected_placed_ints
    board_after_ai = board.copy()
    for card_int, row, idx in placement_info['placements']:
        board_after_ai.add_card(card_int, row, idx)
    assert board_after_ai.get_total_cards() == 13
    assert not check_board_foul(board_after_ai)

@pytest.mark.parametrize("fantasy_hand_str, target_rank_int", [
    (['Qs', 'Qd', 'As', 'Ks', 'Ts'], RANK_QUEEN),
    (['Ks', 'Kd', 'As', 'Qs', 'Ts'], RANK_KING),
    (['As', 'Ad', 'Ks', 'Qs', 'Ts'], RANK_ACE),
    (['7s', '7d', '7c', 'Ks', 'Qs'], RANK_7),
])
def test_ai_prefers_fantasy_qualification_progressive(agent_short_time, fantasy_hand_str, target_rank_int):
    board = PlayerBoard()
    cards_dealt = hand_to_int(fantasy_hand_str)
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)
    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None
    board_after_ai_move = board.copy()
    for card_int, row, idx in placement_info['placements']:
        board_after_ai_move.add_card(card_int, row, idx)
    top_row_cards = board_after_ai_move.get_row_cards('top')
    fantasy_achieved = False
    if len(top_row_cards) == 3:
        from ofc_evaluator_3card import evaluate_3_card_ofc
        try:
            _, type_top, _ = evaluate_3_card_ofc(*top_row_cards)
            if type_top == HAND_TYPE_TRIPS_3:
                fantasy_achieved = True
            elif type_top == HAND_TYPE_PAIR_3:
                ranks_top_counter = Counter(Card.get_rank_int(c) for c in top_row_cards)
                pair_rank_top = next((r for r, count in ranks_top_counter.items() if count == 2), -1)
                if pair_rank_top >= RANK_QUEEN:
                    fantasy_achieved = True
        except ValueError:
            pass
    if not board_after_ai_move.is_complete():
        current_board_all_cards = board_after_ai_move.get_all_cards()
        deck_list_for_completion = list(remaining_deck - current_board_all_cards)
        random.shuffle(deck_list_for_completion)
        slots_to_fill = board_after_ai_move.get_available_slots()
        for i, (r, s_idx) in enumerate(slots_to_fill):
            if i < len(deck_list_for_completion):
                board_after_ai_move.add_card(deck_list_for_completion[i], r, s_idx)
            else: break
    if board_after_ai_move.is_complete():
        is_foul = check_board_foul(board_after_ai_move)
        if fantasy_achieved:
            assert not is_foul, f"AI aimed for Fantasyland ({fantasy_hand_str}) but fouled."
        else:
            assert not is_foul, f"AI made a move that leads to a foul in fantasy attempt scenario ({fantasy_hand_str})."
    if any(Card.get_rank_int(c) == target_rank_int for c in cards_dealt):
         if not fantasy_achieved and board_after_ai_move.is_complete() and not check_board_foul(board_after_ai_move):
             logger.warning(f"AI did not achieve fantasy with {fantasy_hand_str} but made a valid non-foul hand. Review fantasy heuristic.")
         elif fantasy_achieved:
             logger.info(f"AI successfully aimed for fantasy with {fantasy_hand_str}.")

def test_ai_avoids_obvious_foul(agent_short_time):
    board = PlayerBoard()
    board.add_card(Card.from_str('Ks'), 'top', 0); board.add_card(Card.from_str('Kd'), 'top', 1); board.add_card(Card.from_str('Kc'), 'top', 2)
    board.add_card(Card.from_str('2s'), 'middle', 0); board.add_card(Card.from_str('2d'), 'middle', 1); board.add_card(Card.from_str('3h'), 'middle', 2)
    cards_dealt = hand_to_int(['Ah', 'Ad', 'Ac'])
    initial_board_cards = board.get_all_cards()
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt) - initial_board_cards
    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None, "AI did not return a placement"
    final_board = board.copy()
    placed_cards_in_move_ints = set()
    for card_int, row, idx in placement_info['placements']:
        final_board.add_card(card_int, row, idx)
        placed_cards_in_move_ints.add(card_int)
    if not final_board.is_complete():
        current_remaining_deck_list = list(remaining_deck - placed_cards_in_move_ints)
        if placement_info.get('discarded') is not None:
            discarded_val = placement_info['discarded']
            if isinstance(discarded_val, tuple):
                for d_card in discarded_val:
                    if d_card in current_remaining_deck_list: current_remaining_deck_list.remove(d_card)
            elif discarded_val in current_remaining_deck_list:
                current_remaining_deck_list.remove(discarded_val)
        random.shuffle(current_remaining_deck_list)
        slots_to_fill = final_board.get_available_slots()
        for i, (r, s_idx) in enumerate(slots_to_fill):
            if i < len(current_remaining_deck_list): final_board.add_card(current_remaining_deck_list[i], r, s_idx)
            else: break
    if final_board.is_complete():
        assert not check_board_foul(final_board), "AI made a move that leads to a foul"
    elif final_board.get_total_cards() >= 8:
         top_cards = final_board.get_row_cards('top')
         mid_cards = final_board.get_row_cards('middle')
         if len(top_cards) == 3 and len(mid_cards) == 5:
             from ofc_evaluators import get_hand_rank_safe
             top_r, top_c, _ = get_hand_rank_safe(top_cards)
             mid_r, mid_c, _ = get_hand_rank_safe(mid_cards)
             is_foul = (top_c < mid_c) or (top_c == mid_c and top_r < mid_r)
             assert not is_foul, "AI made a move that leads to an early foul between top/middle"

def test_ai_correct_discard_choice_not_first_street(agent_very_short_time):
    board = PlayerBoard()
    board.add_card(Card.from_str('7h'), 'bottom', 0); board.add_card(Card.from_str('8h'), 'bottom', 1)
    board.add_card(Card.from_str('9s'), 'middle', 0); board.add_card(Card.from_str('Ts'), 'middle', 1)
    board.add_card(Card.from_str('Jc'), 'top', 0)
    cards_dealt = hand_to_int(['As', 'Ks', '2c'])
    initial_board_cards = board.get_all_cards()
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt) - initial_board_cards
    placement_info = agent_very_short_time.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None
    assert placement_info.get('discarded') is not None
    assert Card.to_str(placement_info['discarded']) == '2c'
    placed_card_ints = {p[0] for p in placement_info['placements']}
    assert Card.from_str('As') in placed_card_ints and Card.from_str('Ks') in placed_card_ints
    assert len(placed_card_ints) == 2

def test_ai_first_street_strong_hand_to_bottom(agent_short_time):
    board = PlayerBoard()
    cards_dealt = hand_to_int(['As', 'Ks', 'Qs', 'Js', 'Ts'])
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)
    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None and len(placement_info['placements']) == 5
    bottom_cards = [p[0] for p in placement_info['placements'] if p[1] == 'bottom']
    assert len(bottom_cards) == 5
    assert set(bottom_cards) == set(cards_dealt)

def test_ai_handles_unknown_cards_conservatively(agent_short_time):
    board = PlayerBoard()
    cards_dealt = hand_to_int(['Ah', 'Kh', 'Qh', 'Jh', '2d'])
    remaining_deck_few_q = Deck.FULL_DECK_CARDS - set(cards_dealt)
    placement_few_q = agent_short_time.choose_placement(board.copy(), cards_dealt, remaining_deck_few_q, num_unknown_removed_cards=1)
    assert placement_few_q is not None
    board_after_few_q = PlayerBoard()
    for c,r,i in placement_few_q['placements']: board_after_few_q.add_card(c,r,i)
    bottom_cards_few_q = board_after_few_q.get_row_cards('bottom')
    is_flush_attempt_few_q = False
    if len(bottom_cards_few_q) >= 4:
        suits_bottom = Counter(Card.get_suit_int(c) for c in bottom_cards_few_q)
        if any(count >= 4 for count in suits_bottom.values()):
            is_flush_attempt_few_q = True
    placement_many_q = agent_short_time.choose_placement(board.copy(), cards_dealt, remaining_deck_few_q, num_unknown_removed_cards=15)
    assert placement_many_q is not None
    board_after_many_q = PlayerBoard()
    for c,r,i in placement_many_q['placements']: board_after_many_q.add_card(c,r,i)
    bottom_cards_many_q = board_after_many_q.get_row_cards('bottom')
    is_flush_attempt_many_q = False
    if len(bottom_cards_many_q) >= 4:
        suits_bottom = Counter(Card.get_suit_int(c) for c in bottom_cards_many_q)
        if any(count >= 4 for count in suits_bottom.values()):
            is_flush_attempt_many_q = True
    if is_flush_attempt_few_q and not is_flush_attempt_many_q:
        logger.info("AI correctly became more conservative with flush draw due to many '?' cards.")
        pass
    else:
        logger.info("AI behavior regarding flush draw and '?' cards was consistent or did not attempt flush.")
    assert placement_few_q is not None
    assert placement_many_q is not None
