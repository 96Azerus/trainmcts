# tests/test_ofc_logic.py v1.5
"""
Unit-тесты для модуля ofc_logic.py.
Исправлены вызовы check_board_foul и get_row_royalty в тестах.
Исправлен ассерт в test_check_board_foul_logic для board_foul_mt.
"""

import pytest
import random
from typing import List, Optional, Set
from collections import Counter # Добавлен импорт Counter

# Импорты из тестируемого модуля
from ofc_logic import (
    Card, Deck, PlayerBoard,
    check_board_foul, get_row_royalty,
    INVALID_CARD, CARD_PLACEHOLDER, NUM_CARDS,
    ROYALTY_TOP_PAIRS, ROYALTY_TOP_TRIPS,
    ROYALTY_MIDDLE_POINTS, ROYALTY_BOTTOM_POINTS,
    RANK_MAP
)
# Импорты эвалюаторов для тестов скоринга (нужны для hand_to_int и создания карт)
try:
    from ofc_evaluator_3card import evaluate_3_card_ofc
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError:
    pytest.skip("Skipping scoring tests because evaluators could not be imported", allow_module_level=True)


# --- Хелперы ---
def hand_to_int(card_strs: List[Optional[str]]) -> List[Optional[int]]:
    """Конвертирует список строк в список int карт."""
    # Используем метод класса Card
    return Card.hand_to_int(card_strs)

# --- Тесты Card ---
# (Без изменений)
def test_card_from_str_valid():
    assert Card.from_str('As') > 0
    with pytest.raises(TypeError): Card.from_str(12) # type: ignore
    with pytest.raises(ValueError): Card.from_str('A')
    with pytest.raises(ValueError): Card.from_str('Xs')
    with pytest.raises(ValueError): Card.from_str('Ax')

def test_card_to_str_valid():
    assert Card.to_str(Card.from_str('As')) == 'As'
    assert Card.to_str(Card.from_str('Td')) == 'Td'
    assert Card.to_str(Card.from_str('2c')) == '2c'

def test_card_to_str_invalid():
    assert Card.to_str(None) == CARD_PLACEHOLDER
    assert Card.to_str(INVALID_CARD) == CARD_PLACEHOLDER
    assert Card.to_str(-5) == CARD_PLACEHOLDER
    assert Card.to_str(0) == CARD_PLACEHOLDER

def test_card_getters():
    card_int = Card.from_str('Kc')
    assert Card.get_rank_int(card_int) == 11
    assert Card.get_suit_int(card_int) == 8 # 'c'
    assert Card.get_prime(card_int) == 37 # Prime for K

def test_card_hand_conversion():
    strs = ['As', 'Td', None, 'XX', CARD_PLACEHOLDER]
    ints = Card.hand_to_int(strs) # Используем метод класса
    # Ожидаем None для невалидных строк
    assert ints == [Card.from_str('As'), Card.from_str('Td'), None, None, None]
    assert Card.hand_to_str(ints) == ['As', 'Td', CARD_PLACEHOLDER, CARD_PLACEHOLDER, CARD_PLACEHOLDER]


# --- Тесты Deck ---
# (Без изменений)
def test_deck_init_full():
    deck = Deck()
    assert len(deck) == NUM_CARDS
    assert Card.from_str('As') in deck

def test_deck_init_with_cards():
    cards = {Card.from_str('As'), Card.from_str('Ks')}
    deck = Deck(cards=cards)
    assert len(deck) == 2
    assert deck.cards == cards

def test_deck_deal():
    deck = Deck()
    dealt = deck.deal(5)
    assert len(dealt) == 5
    assert len(deck) == NUM_CARDS - 5
    assert all(c not in deck for c in dealt)
    assert len(set(dealt)) == 5

def test_deck_deal_more_than_available():
    deck = Deck(cards={Card.from_str('As'), Card.from_str('Ks')})
    dealt = deck.deal(5)
    assert len(dealt) == 2
    assert len(deck) == 0

def test_deck_remove():
    deck = Deck()
    card_as = Card.from_str('As')
    card_ks = Card.from_str('Ks')
    deck.remove([card_as, card_ks, INVALID_CARD])
    assert len(deck) == NUM_CARDS - 2
    assert card_as not in deck
    assert card_ks not in deck

def test_deck_copy():
    deck1 = Deck(); deck1.deal(10)
    deck2 = deck1.copy()
    assert deck1 is not deck2
    assert deck1.cards is not deck2.cards
    assert deck1.cards == deck2.cards
    deck2.deal(5)
    assert len(deck1) == NUM_CARDS - 10
    assert len(deck2) == NUM_CARDS - 15


# --- Тесты PlayerBoard ---
# (Без изменений)
def test_playerboard_init():
    board = PlayerBoard()
    assert board.get_total_cards() == 0
    assert not board.is_complete()
    assert not board.is_foul
    assert len(board.get_available_slots()) == PlayerBoard.TOTAL_CAPACITY

def test_playerboard_add_card():
    board = PlayerBoard()
    card_as = Card.from_str('As')
    assert board.add_card(card_as, 'top', 0)
    assert board.get_total_cards() == 1
    assert board.rows['top'][0] == card_as
    assert not board.add_card(Card.from_str('Ks'), 'top', 0) # Занято
    assert not board.add_card(Card.from_str('Ks'), 'top', 3) # Неверный индекс
    assert not board.add_card(INVALID_CARD, 'middle', 0) # Невалидная карта

def test_playerboard_set_full_board():
    board = PlayerBoard()
    # Убедимся, что карты уникальны
    top_s = ['Ah', 'Ad', 'Ac']
    mid_s = ['Ks', 'Kd', 'Qc', 'Qd', '2s']
    bot_s = ['As', 'Kh', 'Qs', 'Js', 'Ts']
    all_s = top_s + mid_s + bot_s
    assert len(all_s) == len(set(all_s)), "Duplicate cards in test data for set_full_board"

    top = hand_to_int(top_s)
    middle = hand_to_int(mid_s)
    bottom = hand_to_int(bot_s)

    # Убираем Optional[int] из сигнатуры, так как set_full_board ожидает только int
    board.set_full_board(
        [c for c in top if c is not None],
        [c for c in middle if c is not None],
        [c for c in bottom if c is not None]
    )
    assert board.is_complete()
    assert board.get_total_cards() == 13
    assert board.rows['top'] == top
    with pytest.raises(ValueError): # Дубликат
        board.set_full_board(
            [c for c in top if c is not None],
            [c for c in middle if c is not None],
            hand_to_int(['Ah', 'Kh', 'Qs', 'Js', 'Ts']) # type: ignore
        )
    with pytest.raises(ValueError): # Неверное кол-во
        board.set_full_board(
            [c for c in top[:2] if c is not None],
            [c for c in middle if c is not None],
            [c for c in bottom if c is not None]
        )


def test_playerboard_get_available_slots():
    board = PlayerBoard()
    board.add_card(Card.from_str('As'), 'top', 0)
    board.add_card(Card.from_str('Ks'), 'bottom', 4)
    slots = board.get_available_slots()
    assert len(slots) == PlayerBoard.TOTAL_CAPACITY - 2
    assert ('top', 0) not in slots
    assert ('bottom', 4) not in slots
    assert ('top', 1) in slots
    assert ('middle', 0) in slots

def test_playerboard_get_board_state_tuple():
    board = PlayerBoard()
    board.add_card(Card.from_str('As'), 'top', 0)
    board.add_card(Card.from_str('2c'), 'top', 2)
    board.add_card(Card.from_str('Kd'), 'middle', 1)
    state_tuple = board.get_board_state_tuple()
    # Проверяем содержимое, порядок должен сохраняться
    assert state_tuple[0] == (Card.from_str('As'), None, Card.from_str('2c'))
    assert state_tuple[1] == (None, Card.from_str('Kd'), None, None, None)
    assert state_tuple[2] == (None, None, None, None, None)


def test_playerboard_copy():
    board1 = PlayerBoard()
    board1.add_card(Card.from_str('As'), 'top', 0)
    board2 = board1.copy()
    assert board1 is not board2
    assert board1.rows is not board2.rows
    assert board1.rows['top'] is not board2.rows['top']
    assert board1.rows == board2.rows
    board2.add_card(Card.from_str('Ks'), 'top', 1)
    assert board1.get_total_cards() == 1
    assert board2.get_total_cards() == 2


# --- Тесты Scoring ---
@pytest.mark.skipif('evaluate_3_card_ofc' not in globals() or 'evaluator_5card' not in globals(),
                    reason="Evaluators not imported, skipping scoring tests")
def test_check_board_foul_logic():
    # Валидная доска
    board_ok = PlayerBoard()
    board_ok.set_full_board(
        hand_to_int(['Qh', 'Qd', '2c']), # Pair Q (AdjRank ~7464+40=7504)
        hand_to_int(['Ah', 'Kh', 'Th', 'Jh', '9h']), # Flush A high (Rank ~500-1000)
        hand_to_int(['As', 'Ad', 'Ac', 'Ks', 'Kd'])  # FH A over K (Rank 167)
    )
    # FIX 5: Убран вызов check_board_foul с эвалюаторами
    board_ok.is_foul = check_board_foul(board_ok)
    assert not board_ok.is_foul, f"Board should be valid: {board_ok}"

    # Фол: Middle > Top (Flush A > Pair Q) - ЭТО НЕ ФОЛ!
    board_foul_mt = PlayerBoard()
    board_foul_mt.set_full_board(
        hand_to_int(['2h', '3d', '4c']), # High Card 4 (AdjRank ~7464+454=7918) - Слабая
        hand_to_int(['As', 'Ad', 'Kc', 'Kd', 'Qc']), # Two Pair AK (Rank 2468) - Средняя
        hand_to_int(['Ah', 'Kh', 'Qh', 'Jh', 'Th'])  # Straight Flush A (Rank 1) - Сильная
    )
    # FIX 5: Убран вызов check_board_foul с эвалюаторами
    board_foul_mt.is_foul = check_board_foul(board_foul_mt)
    # --- ИСПРАВЛЕНО: Ассерт и сообщение ---
    assert not board_foul_mt.is_foul, f"Board should be valid (Top > Middle > Bottom): {board_foul_mt}"

    # Фол: Bottom > Middle (Flush K < Two Pair KQ)
    board_foul_bm = PlayerBoard()
    board_foul_bm.set_full_board(
        hand_to_int(['Ah', 'Ad', 'Ac']), # Trips A (AdjRank ~7464+1=7465) - Слабая
        hand_to_int(['Ks', 'Kd', 'Qc', 'Qd', '2s']), # Two Pair KQ (Rank ~2500) - Средняя
        hand_to_int(['Th', 'Jh', 'Qh', 'Kh', '9h'])  # Flush K (Rank ~1000-1500) - Сильная
    )
    # FIX 5: Убран вызов check_board_foul с эвалюаторами
    board_foul_bm.is_foul = check_board_foul(board_foul_bm)
    # --- ИСПРАВЛЕНО: Проверяем, что это фол ---
    assert board_foul_bm.is_foul, f"Board should be foul (Bottom < Middle): {board_foul_bm}" # Bottom < Middle -> Foul

    # Неполная доска - не фол
    board_incomplete = PlayerBoard()
    board_incomplete.add_card(Card.from_str('As'), 'top', 0)
    # FIX 5: Убран вызов check_board_foul с эвалюаторами
    board_incomplete.is_foul = check_board_foul(board_incomplete)
    assert not board_incomplete.is_foul

@pytest.mark.skipif('evaluate_3_card_ofc' not in globals() or 'evaluator_5card' not in globals(),
                    reason="Evaluators not imported, skipping scoring tests")
def test_get_row_royalty_logic():
    # FIX 6: Убраны эвалюаторы из вызовов get_row_royalty
    # Top
    assert get_row_royalty(hand_to_int(['Ah', 'Ad', 'Ac']), 'top') == 22
    assert get_row_royalty(hand_to_int(['Qh', 'Qd', '2c']), 'top') == 7
    assert get_row_royalty(hand_to_int(['6h', '6d', 'Ac']), 'top') == 1
    assert get_row_royalty(hand_to_int(['5h', '5d', 'Ac']), 'top') == 0
    assert get_row_royalty(hand_to_int(['Ah', 'Kc', 'Qd']), 'top') == 0
    # Middle
    assert get_row_royalty(hand_to_int(['As','Ks','Qs','Js','Ts']), 'middle') == 50 # RF
    assert get_row_royalty(hand_to_int(['Ac','Ad','Ah','As','2c']), 'middle') == 20 # Quads
    assert get_row_royalty(hand_to_int(['Kc','Kd','Kh','Qc','Qs']), 'middle') == 12 # FH
    assert get_row_royalty(hand_to_int(['Ac','Ad','Ah','Ks','Qd']), 'middle') == 2 # Trips
    assert get_row_royalty(hand_to_int(['Ac','Ad','Kc','Kd','2s']), 'middle') == 0 # 2 Pair
    # Bottom
    assert get_row_royalty(hand_to_int(['As','Ks','Qs','Js','Ts']), 'bottom') == 25 # RF
    assert get_row_royalty(hand_to_int(['Ac','Ad','Ah','As','2c']), 'bottom') == 10 # Quads
    assert get_row_royalty(hand_to_int(['Kc','Kd','Kh','Qc','Qs']), 'bottom') == 6 # FH
    assert get_row_royalty(hand_to_int(['Ac','Ad','Ah','Ks','Qd']), 'bottom') == 0 # Trips (no royalty)
    # Невалидные/неполные
    assert get_row_royalty(hand_to_int(['As', 'Ks', None]), 'top') == 0
    assert get_row_royalty(hand_to_int(['As', 'Ks', 'Qs', 'Js', None]), 'middle') == 0
    assert get_row_royalty(hand_to_int(['As', 'As', 'Ks']), 'top') == 0 # Дубликат
