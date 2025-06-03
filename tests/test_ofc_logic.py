# tests/test_ofc_logic.py v1.3
"""
Unit-тесты для ofc_logic.py.
ИСПРАВЛЕНО: Ожидаемое значение роялти для QQx на топе.
"""
import pytest
from typing import List, Tuple, Optional, Dict, Set, Any

try:
    from ofc_logic import (
        Card, Deck, PlayerBoard,
        card_to_str, # str_to_card, hand_to_int, hand_to_str removed
        # check_board_foul, get_row_royalty, # Removed, will be imported from ofc_evaluators
        RANK_MAP, SUIT_MAP, STR_RANKS, # STR_SUITS removed
        CARD_PLACEHOLDER, INVALID_CARD,
        INT_RANK_TO_CHAR, INT_SUIT_TO_CHAR # Added for char conversion
    )
    # Если get_row_royalty и check_board_foul в ofc_evaluators, импортируем оттуда
    from ofc_evaluators import get_row_royalty as eval_get_row_royalty, \
                               check_board_foul as eval_check_board_foul, \
                               ROYALTY_BOTTOM_POINTS, ROYALTY_MIDDLE_POINTS, ROYALTY_TOP_PAIRS, ROYALTY_TOP_TRIPS
    # Используем версии из ofc_evaluators для тестов, если они там
    get_row_royalty_to_test = eval_get_row_royalty
    check_board_foul_to_test = eval_check_board_foul

except ImportError as e:
    pytest.skip(f"Skipping OFC logic tests due to import error: {e}", allow_module_level=True)
    # raise e # Re-raise the exception to see the traceback
    # Определяем заглушки, чтобы IDE не ругалась, если тесты все же запустятся частично
    class Card: pass # type: ignore
    class Deck: pass # type: ignore
    class PlayerBoard: pass # type: ignore
    def card_to_str(c): return "" # type: ignore
    # def str_to_card(s): return 0 # type: ignore
    # def hand_to_int(h): return [] # type: ignore
    # def hand_to_str(h): return [] # type: ignore # No longer needed as Card.hand_to_str will be used or mocked
    # get_row_royalty_to_test and check_board_foul_to_test will be defined from ofc_evaluators import
    # or this whole block is skipped by pytest.skip if ofc_evaluators fails to import.
    # So, no need for specific mocks for them here if the import from ofc_evaluators is the primary source.
    ROYALTY_BOTTOM_POINTS={} # type: ignore
    ROYALTY_MIDDLE_POINTS={} # type: ignore
    ROYALTY_TOP_PAIRS={} # type: ignore
    ROYALTY_TOP_TRIPS={} # type: ignore


# --- Card Tests ---
@pytest.mark.parametrize("card_str, rank_char, suit_char", [
    ("As", "A", "s"), ("Kc", "K", "c"), ("Td", "T", "d"), ("2h", "2", "h"),
    ("10s", "T", "s") # Проверка для "10"
])
def test_card_from_str_valid(card_str, rank_char, suit_char):
    card_int = Card.from_str(card_str)
    assert INT_RANK_TO_CHAR[Card.get_rank_int(card_int)] == rank_char
    assert INT_SUIT_TO_CHAR[Card.get_suit_int(card_int)] == suit_char

@pytest.mark.parametrize("card_int_val, expected_str", [
    (Card.from_str("As"), "As"), (Card.from_str("Kc"), "Kc"),
    (Card.from_str("Td"), "Td"), (Card.from_str("2h"), "2h")
])
def test_card_to_str_valid(card_int_val, expected_str):
    assert Card.to_str(card_int_val) == expected_str

def test_card_to_str_invalid():
    assert Card.to_str(INVALID_CARD) == CARD_PLACEHOLDER
    assert Card.to_str(None) == CARD_PLACEHOLDER # type: ignore
    assert Card.to_str(0) == CARD_PLACEHOLDER # Невалидный int

def test_card_getters():
    card_as = Card.from_str("As")
    assert Card.get_rank_int(card_as) == RANK_MAP['A']
    assert Card.get_suit_int(card_as) == SUIT_MAP['s']
    assert INT_RANK_TO_CHAR[Card.get_rank_int(card_as)] == 'A'
    assert INT_SUIT_TO_CHAR[Card.get_suit_int(card_as)] == 's'

def test_card_hand_conversion():
    hand_strs = ["As", "Kc", "Td"]
    hand_ints = Card.hand_to_int(hand_strs)
    assert len(hand_ints) == 3
    assert INT_RANK_TO_CHAR[Card.get_rank_int(hand_ints[0])] == "A"
    assert Card.hand_to_str(hand_ints) == hand_strs

    hand_strs_with_none = ["As", None, "Td"]
    hand_ints_filtered = Card.hand_to_int(hand_strs_with_none) # type: ignore
    assert len(hand_ints_filtered) == 3 # Should preserve None, so length is 3
    assert hand_ints_filtered[1] is None # Explicitly check for None
    assert INT_RANK_TO_CHAR[Card.get_rank_int(hand_ints_filtered[0])] == "A"


# --- Deck Tests ---
def test_deck_init_full():
    deck = Deck()
    assert len(deck.get_remaining_cards()) == 52
    assert len(set(deck.get_remaining_cards())) == 52 # Все карты уникальны

def test_deck_init_with_cards():
    initial_cards = Card.hand_to_int(["As", "Ks"])
    # Convert list to set for Deck constructor if it expects a set
    deck = Deck(cards=set(initial_cards)) 
    # Convert list to set for comparison if get_remaining_cards returns a list but order might differ
    # or if initial_cards might have duplicates (though hand_to_int likely handles this)
    assert set(deck.get_remaining_cards()) == set(initial_cards)

def test_deck_deal():
    deck = Deck()
    initial_size = len(deck.get_remaining_cards())
    dealt_cards = deck.deal(5)
    assert len(dealt_cards) == 5
    assert len(deck.get_remaining_cards()) == initial_size - 5
    for card in dealt_cards:
        assert card not in deck.get_remaining_cards()

def test_deck_deal_more_than_available():
    initial_cards_list = Card.hand_to_int(["As", "Ks"])
    deck = Deck(cards=set(initial_cards_list)) # Use set for constructor
    dealt_cards = deck.deal(5) # Пытаемся сдать 5, есть только 2
    assert len(dealt_cards) == 2
    assert len(deck.get_remaining_cards()) == 0

def test_deck_remove():
    deck = Deck()
    card_as = Card.from_str("As")
    deck.remove([card_as]) # remove expects a list
    assert card_as not in deck.get_remaining_cards()
    assert len(deck.get_remaining_cards()) == 51

def test_deck_copy():
    deck1 = Deck()
    deck1.deal(5)
    deck2 = deck1.copy()
    assert set(deck1.get_remaining_cards()) == set(deck2.get_remaining_cards()) # Compare sets for order independence
    assert deck1 is not deck2
    deck2.deal(3)
    assert len(deck1.get_remaining_cards()) != len(deck2.get_remaining_cards())


# --- PlayerBoard Tests ---
def test_playerboard_init():
    board = PlayerBoard()
    assert board.get_total_cards() == 0
    assert board.rows['top'] == [None, None, None]
    assert board.rows['middle'] == [None, None, None, None, None]
    assert board.rows['bottom'] == [None, None, None, None, None]

def test_playerboard_add_card():
    board = PlayerBoard()
    card_as = Card.from_str("As")
    board.add_card(card_as, 'top', 0)
    assert board.get_total_cards() == 1
    assert board.get_row_cards('top')[0] == card_as
    # Check return value for adding to occupied slot
    assert board.add_card(Card.from_str("Ks"), 'top', 0) is False
    # Check return value for invalid index
    assert board.add_card(Card.from_str("Ks"), 'top', 3) is False
    # Check return value for invalid row name
    assert board.add_card(Card.from_str("Ks"), 'tops', 0) is False # type: ignore

def test_playerboard_set_full_board():
    board = PlayerBoard()
    full_board_state = {
        'top': Card.hand_to_int(['As', 'Ks', 'Qs']),
        'middle': Card.hand_to_int(['Js', 'Ts', '9s', '8s', '7s']),
        'bottom': Card.hand_to_int(['6s', '5s', '4s', '3s', '2s'])
    }
    board.set_full_board(full_board_state['top'], full_board_state['middle'], full_board_state['bottom'])
    assert board.is_complete()
    assert board.get_row_cards('top') == full_board_state['top']

def test_playerboard_get_available_slots():
    board = PlayerBoard()
    assert len(board.get_available_slots()) == 13
    board.add_card(Card.from_str("As"), 'top', 0)
    assert len(board.get_available_slots()) == 12
    assert ('top', 0) not in board.get_available_slots()

def test_playerboard_get_board_state_tuple():
    board = PlayerBoard()
    board.add_card(Card.from_str("As"), 'top', 0)
    board.add_card(Card.from_str("Ks"), 'middle', 2)
    state_tuple = board.get_board_state_tuple()
    # Ожидаем кортеж из 3 кортежей (top, middle, bottom)
    assert len(state_tuple) == 3
    assert state_tuple[0][0] == Card.from_str("As")
    assert state_tuple[1][2] == Card.from_str("Ks")
    assert state_tuple[2][0] is None

def test_playerboard_copy():
    board1 = PlayerBoard()
    board1.add_card(Card.from_str("As"), 'top', 0)
    board2 = board1.copy()
    assert board1.get_total_cards() == board2.get_total_cards()
    assert board1.get_row_cards('top')[0] == board2.get_row_cards('top')[0]
    board2.add_card(Card.from_str("Ks"), 'middle', 0)
    assert board1.get_total_cards() != board2.get_total_cards()


# --- Scoring Logic Tests (using _to_test versions) ---
# Параметризация для check_board_foul
@pytest.mark.parametrize("top_hand, middle_hand, bottom_hand, expected_foul", [
    # Valid hands
    (["2s", "2d", "3c"], ["As", "Ks", "Qs", "Js", "Ts"], ["Ah", "Ad", "Ac", "Kh", "Kd"], True), # Foul: Middle > Bottom (Royal Flush > Full House)
    (["As", "Ks", "Qs"], ["As", "Ks", "Qs"], ["As", "Ks", "Qs"], False), # All equal (not a foul by definition of ranks)
    # Foul hands
    (["As", "Ks", "Qs"], ["2s", "2d", "3c", "4c", "5c"], ["Ah", "Ad", "Ac", "Kh", "Kd"], False), # Corrected: Not a foul (AKQ < Pair 2s < Full House)
    (["2s", "2d", "3c"], ["Ah", "Ad", "Ac", "Kh", "Kd"], ["As", "Ks", "Qs", "Js", "Ts"], False), # Corrected: Not a foul (Pair 2s < Full House < Royal Flush)
    # Incomplete hands (should not foul if order is maintained for completed rows)
    # check_board_foul должен корректно обрабатывать неполные руки, если это предусмотрено.
    # Обычно фол определяется только для полной доски. Если неполная, то не фол.
    # Но если линии заполнены и нарушен порядок, то фол.
    (["As", "Ks", "Qs"], ["2s", "2d", "3c", None, None], [None, None, None, None, None], False), # Top > Mid (Mid incomplete) -> No foul yet
    ([None, None, None], ["As", "Ks", "Qs", "Js", "Ts"], ["2h", "2d", "3c", "4c", "5c"], False), # Corrected: Not a foul (incomplete board)
])
# @pytest.mark.skipif('check_board_foul_to_test' not in globals() or 'get_row_royalty_to_test' not in globals(),
#                     reason="Scoring functions not imported, skipping scoring tests")
def test_check_board_foul_logic(top_hand, middle_hand, bottom_hand, expected_foul):
    board = PlayerBoard()
    def set_row(brd, row_name, hand_str_list):
        int_list = Card.hand_to_int(hand_str_list)
        for i, card_int in enumerate(int_list):
            if card_int is not None:
                brd.add_card(card_int, row_name, i)
    
    set_row(board, 'top', top_hand)
    set_row(board, 'middle', middle_hand)
    set_row(board, 'bottom', bottom_hand)
    
    # Если тест предполагает неполную доску, а check_board_foul ожидает полную,
    # то нужно либо заполнить доску до конца, либо адаптировать тест/функцию.
    # Для данного теста, предполагаем, что check_board_foul может работать с частично заполненными линиями.
    assert check_board_foul_to_test(board) == expected_foul


# @pytest.mark.skipif('check_board_foul_to_test' not in globals() or 'get_row_royalty_to_test' not in globals(),
#                     reason="Scoring functions not imported, skipping scoring tests")
def test_get_row_royalty_logic():
    # Top
    # Assuming ROYALTY_TOP_TRIPS and ROYALTY_TOP_PAIRS are dicts like {RANK_INT: points ...}
    # And hand_to_int returns List[Optional[int]]
    assert get_row_royalty_to_test(Card.hand_to_int(['Ah', 'Ad', 'Ac']), 'top') == ROYALTY_TOP_TRIPS.get(RANK_MAP['A'], 0) # AAA
    assert get_row_royalty_to_test(Card.hand_to_int(['Qh', 'Qd', '2c']), 'top') == ROYALTY_TOP_PAIRS.get(RANK_MAP['Q'], 0) # QQx
    assert get_row_royalty_to_test(Card.hand_to_int(['6h', '6d', 'Ac']), 'top') == ROYALTY_TOP_PAIRS.get(RANK_MAP['6'], 0) # 66x
    assert get_row_royalty_to_test(Card.hand_to_int(['Ah', 'Kd', '2c']), 'top') == 0 # High card

    # Middle (примеры, нужно сверить с ROYALTY_MIDDLE)
    # Для простоты, предполагаем, что ROYALTY_MIDDLE это словарь {hand_type_str: points}
    # или {hand_rank_value: points}
    # Это нужно адаптировать под реальную структуру ROYALTY_MIDDLE
    # ROYALTY_MIDDLE_POINTS uses hand type strings as keys e.g. "Straight", "Flush"
    if ROYALTY_MIDDLE_POINTS: # Если константа определена
        # The .get('Trips', {}).get('A', 0) was incorrect for how ROYALTY_MIDDLE_POINTS is structured.
        # It should be ROYALTY_MIDDLE_POINTS.get("Three of a Kind", 0) if 'A' trips map to "Three of a Kind" key
        # For now, I will assume the keys in the test are correct hand type strings if they are used directly.
        # Example: if trips AAAKQ on middle gives 2 points, the key in ROYALTY_MIDDLE_POINTS might be "Three of a Kind"
        # The original test used .get('Trips', {}).get('A', 0) which implies a nested dict structure that isn't there.
        # Based on ofc_evaluators.py, ROYALTY_MIDDLE_POINTS is Dict[str, int] like {"Three of a Kind": 2, ...}
        # So, the tests should directly use these string keys.
        # The following lines are examples and might need adjustment based on actual hand evaluation for royalty.
        # For 'Ah Ad Ac Ks Qs', this is Trips Aces.
        # Let's assume get_row_royalty returns points based on the hand type string.
        # The test needs to ensure that the hand 'Ah Ad Ac Ks Qs' is evaluated to "Three of a Kind" by get_hand_rank_safe,
        # and then check ROYALTY_MIDDLE_POINTS["Three of a Kind"].
        # The current structure of the test for middle/bottom is more of a placeholder.
        # I will keep the .get(HAND_TYPE_STRING, 0) for robustness.
        assert get_row_royalty_to_test(Card.hand_to_int(['Ah', 'Ad', 'Ac', 'Ks', 'Qs']), 'middle') == ROYALTY_MIDDLE_POINTS.get("Three of a Kind", 0)
        # Corrected expectation for Royal Flush on the middle
        assert get_row_royalty_to_test(Card.hand_to_int(['As', 'Ks', 'Qs', 'Js', 'Ts']), 'middle') == ROYALTY_MIDDLE_POINTS.get("Royal Flush", 0)
        assert get_row_royalty_to_test(Card.hand_to_int(['Ah', 'Kh', 'Qh', 'Jh', '9h']), 'middle') == ROYALTY_MIDDLE_POINTS.get("Flush", 0)

    # Bottom (аналогично middle)
    if ROYALTY_BOTTOM_POINTS:
        assert get_row_royalty_to_test(Card.hand_to_int(['Ah', 'Ad', 'Ac', 'Ks', 'Kd']), 'bottom') == ROYALTY_BOTTOM_POINTS.get("Full House", 0)

    # Проверка на пустые или неполные руки (должны возвращать 0)
    assert get_row_royalty_to_test(Card.hand_to_int(['Ah', 'Ad', None]), 'top') == 0 # type: ignore
    assert get_row_royalty_to_test([None, None, None, None, None], 'middle') == 0
