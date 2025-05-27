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
        card_to_str, str_to_card, hand_to_int, hand_to_str,
        check_board_foul, get_row_royalty, # Предполагаем, что они здесь или импортируются в ofc_logic
        RANK_MAP, SUIT_MAP, STR_RANKS, STR_SUITS,
        CARD_PLACEHOLDER, INVALID_CARD
    )
    # Если get_row_royalty и check_board_foul в ofc_evaluators, импортируем оттуда
    from ofc_evaluators import get_row_royalty as eval_get_row_royalty, \
                               check_board_foul as eval_check_board_foul, \
                               ROYALTY_BOTTOM, ROYALTY_MIDDLE, ROYALTY_TOP_PAIRS, ROYALTY_TOP_TRIPS
    # Используем версии из ofc_evaluators для тестов, если они там
    get_row_royalty_to_test = eval_get_row_royalty
    check_board_foul_to_test = eval_check_board_foul

except ImportError as e:
    pytest.skip(f"Skipping OFC logic tests due to import error: {e}", allow_module_level=True)
    # Определяем заглушки, чтобы IDE не ругалась, если тесты все же запустятся частично
    class Card: pass # type: ignore
    class Deck: pass # type: ignore
    class PlayerBoard: pass # type: ignore
    def card_to_str(c): return "" # type: ignore
    def str_to_card(s): return 0 # type: ignore
    def hand_to_int(h): return [] # type: ignore
    def hand_to_str(h): return [] # type: ignore
    def get_row_royalty_to_test(h, r): return 0 # type: ignore
    def check_board_foul_to_test(b): return False # type: ignore
    ROYALTY_BOTTOM={}, ROYALTY_MIDDLE={}, ROYALTY_TOP_PAIRS={}, ROYALTY_TOP_TRIPS={} # type: ignore


# --- Card Tests ---
@pytest.mark.parametrize("card_str, rank_char, suit_char", [
    ("As", "A", "s"), ("Kc", "K", "c"), ("Td", "T", "d"), ("2h", "2", "h"),
    ("10s", "T", "s") # Проверка для "10"
])
def test_card_from_str_valid(card_str, rank_char, suit_char):
    card_int = Card.from_str(card_str)
    assert Card.get_rank_char(card_int) == rank_char
    assert Card.get_suit_char(card_int) == suit_char

@pytest.mark.parametrize("card_int_val, expected_str", [
    (Card.from_str("As"), "As"), (Card.from_str("Kc"), "Kc"),
    (Card.from_str("Td"), "Td"), (Card.from_str("2h"), "2h")
])
def test_card_to_str_valid(card_int_val, expected_str):
    assert Card.to_str(card_int_val) == expected_str

def test_card_to_str_invalid():
    assert Card.to_str(INVALID_CARD) == "??"
    assert Card.to_str(None) == "??" # type: ignore
    assert Card.to_str(0) == "??" # Невалидный int

def test_card_getters():
    card_as = Card.from_str("As")
    assert Card.get_rank_int(card_as) == RANK_MAP['A']
    assert Card.get_suit_int(card_as) == SUIT_MAP['s']
    assert Card.get_rank_char(card_as) == 'A'
    assert Card.get_suit_char(card_as) == 's'

def test_card_hand_conversion():
    hand_strs = ["As", "Kc", "Td"]
    hand_ints = hand_to_int(hand_strs)
    assert len(hand_ints) == 3
    assert Card.get_rank_char(hand_ints[0]) == "A"
    assert hand_to_str(hand_ints) == hand_strs

    hand_strs_with_none = ["As", None, "Td"]
    hand_ints_filtered = hand_to_int(hand_strs_with_none) # type: ignore
    assert len(hand_ints_filtered) == 2
    assert Card.get_rank_char(hand_ints_filtered[0]) == "A"


# --- Deck Tests ---
def test_deck_init_full():
    deck = Deck()
    assert len(deck.get_cards()) == 52
    assert len(set(deck.get_cards())) == 52 # Все карты уникальны

def test_deck_init_with_cards():
    initial_cards = hand_to_int(["As", "Ks"])
    deck = Deck(cards=initial_cards)
    assert deck.get_cards() == initial_cards

def test_deck_deal():
    deck = Deck()
    initial_size = len(deck.get_cards())
    dealt_cards = deck.deal(5)
    assert len(dealt_cards) == 5
    assert len(deck.get_cards()) == initial_size - 5
    for card in dealt_cards:
        assert card not in deck.get_cards()

def test_deck_deal_more_than_available():
    deck = Deck(cards=hand_to_int(["As", "Ks"]))
    dealt_cards = deck.deal(5) # Пытаемся сдать 5, есть только 2
    assert len(dealt_cards) == 2
    assert len(deck.get_cards()) == 0

def test_deck_remove():
    deck = Deck()
    card_as = Card.from_str("As")
    deck.remove_card(card_as)
    assert card_as not in deck.get_cards()
    assert len(deck.get_cards()) == 51

def test_deck_copy():
    deck1 = Deck()
    deck1.deal(5)
    deck2 = deck1.copy()
    assert deck1.get_cards() == deck2.get_cards()
    assert deck1 is not deck2
    deck2.deal(3)
    assert len(deck1.get_cards()) != len(deck2.get_cards())


# --- PlayerBoard Tests ---
def test_playerboard_init():
    board = PlayerBoard()
    assert board.get_total_cards() == 0
    assert board.get_row_cards('top') == [None, None, None]
    assert board.get_row_cards('middle') == [None, None, None, None, None]
    assert board.get_row_cards('bottom') == [None, None, None, None, None]

def test_playerboard_add_card():
    board = PlayerBoard()
    card_as = Card.from_str("As")
    board.add_card(card_as, 'top', 0)
    assert board.get_total_cards() == 1
    assert board.get_row_cards('top')[0] == card_as
    with pytest.raises(ValueError): # Попытка добавить в занятый слот
        board.add_card(Card.from_str("Ks"), 'top', 0)
    with pytest.raises(ValueError): # Неверный индекс
        board.add_card(Card.from_str("Ks"), 'top', 3)
    with pytest.raises(KeyError): # Неверное имя ряда
        board.add_card(Card.from_str("Ks"), 'tops', 0) # type: ignore

def test_playerboard_set_full_board():
    board = PlayerBoard()
    full_board_state = {
        'top': hand_to_int(['As', 'Ks', 'Qs']),
        'middle': hand_to_int(['Js', 'Ts', '9s', '8s', '7s']),
        'bottom': hand_to_int(['6s', '5s', '4s', '3s', '2s'])
    }
    board.set_board_state(full_board_state)
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
    (["2s", "2d", "3c"], ["As", "Ks", "Qs", "Js", "Ts"], ["Ah", "Ad", "Ac", "Kh", "Kd"], False), # Top < Mid < Bot
    (["As", "Ks", "Qs"], ["As", "Ks", "Qs"], ["As", "Ks", "Qs"], False), # All equal (not a foul by definition of ranks)
    # Foul hands
    (["As", "Ks", "Qs"], ["2s", "2d", "3c", "4c", "5c"], ["Ah", "Ad", "Ac", "Kh", "Kd"], True), # Top > Mid
    (["2s", "2d", "3c"], ["Ah", "Ad", "Ac", "Kh", "Kd"], ["As", "Ks", "Qs", "Js", "Ts"], True), # Mid > Bot
    # Incomplete hands (should not foul if order is maintained for completed rows)
    # check_board_foul должен корректно обрабатывать неполные руки, если это предусмотрено.
    # Обычно фол определяется только для полной доски. Если неполная, то не фол.
    # Но если линии заполнены и нарушен порядок, то фол.
    (["As", "Ks", "Qs"], ["2s", "2d", "3c", None, None], [None, None, None, None, None], False), # Top > Mid (Mid incomplete) -> No foul yet
    ([None, None, None], ["As", "Ks", "Qs", "Js", "Ts"], ["2h", "2d", "3c", "4c", "5c"], True), # Mid > Bot (Top incomplete)
])
# @pytest.mark.skipif('check_board_foul_to_test' not in globals() or 'get_row_royalty_to_test' not in globals(),
#                     reason="Scoring functions not imported, skipping scoring tests")
def test_check_board_foul_logic(top_hand, middle_hand, bottom_hand, expected_foul):
    board = PlayerBoard()
    def set_row(brd, row_name, hand_str_list):
        int_list = hand_to_int(hand_str_list)
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
    assert get_row_royalty_to_test(hand_to_int(['Ah', 'Ad', 'Ac']), 'top') == ROYALTY_TOP_TRIPS['A'] # AAA
    # ИСПРАВЛЕНО: Ожидаемое значение для QQx на топе
    assert get_row_royalty_to_test(hand_to_int(['Qh', 'Qd', '2c']), 'top') == ROYALTY_TOP_PAIRS['Q'] # QQx -> 10
    assert get_row_royalty_to_test(hand_to_int(['6h', '6d', 'Ac']), 'top') == ROYALTY_TOP_PAIRS['6'] # 66x
    assert get_row_royalty_to_test(hand_to_int(['Ah', 'Kd', '2c']), 'top') == 0 # High card

    # Middle (примеры, нужно сверить с ROYALTY_MIDDLE)
    # Для простоты, предполагаем, что ROYALTY_MIDDLE это словарь {hand_type_str: points}
    # или {hand_rank_value: points}
    # Это нужно адаптировать под реальную структуру ROYALTY_MIDDLE
    if ROYALTY_MIDDLE: # Если константа определена
        assert get_row_royalty_to_test(hand_to_int(['Ah', 'Ad', 'Ac', 'Ks', 'Qs']), 'middle') == ROYALTY_MIDDLE.get('Trips', {}).get('A', 2) # AAAKQ -> Сет тузов
        assert get_row_royalty_to_test(hand_to_int(['As', 'Ks', 'Qs', 'Js', 'Ts']), 'middle') == ROYALTY_MIDDLE.get('Straight', 4) # Straight A-T
        assert get_row_royalty_to_test(hand_to_int(['Ah', 'Kh', 'Qh', 'Jh', '9h']), 'middle') == ROYALTY_MIDDLE.get('Flush', 8) # Flush A high
        # ... другие комбинации для middle

    # Bottom (аналогично middle)
    if ROYALTY_BOTTOM:
        assert get_row_royalty_to_test(hand_to_int(['Ah', 'Ad', 'Ac', 'Ks', 'Kd']), 'bottom') == ROYALTY_BOTTOM.get('Full House', 6) # Full House AAKK
        # ... другие комбинации для bottom

    # Проверка на пустые или неполные руки (должны возвращать 0)
    assert get_row_royalty_to_test(hand_to_int(['Ah', 'Ad', None]), 'top') == 0 # type: ignore
    assert get_row_royalty_to_test([None, None, None, None, None], 'middle') == 0
