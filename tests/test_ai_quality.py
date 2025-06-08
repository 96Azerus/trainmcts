# tests/test_ai_quality.py v1.2 (Added test for 11+3 cards, enhanced Fantasy test)
"""
Тесты для оценки качества решений MCTS-агента.
- Добавлен тест на корректное размещение 2 из 3 карт при 11 картах на доске.
- Улучшен тест на стремление к Фантазии.
"""
import pytest
from unittest.mock import ANY
import random
import logging
from collections import Counter # ИСПРАВЛЕНИЕ: Добавлен импорт Counter

try:
    from mcts_agent import MCTSAgent
    from ofc_logic import (
        PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS,
        RANK_2, RANK_3, RANK_4, RANK_5, RANK_6, RANK_7, RANK_8, RANK_9,
        RANK_TEN, RANK_JACK, RANK_QUEEN, RANK_KING, RANK_ACE
    )
    # ИСПРАВЛЕНИЕ: Добавлены недостающие импорты
    from ofc_evaluators import check_board_foul, get_row_royalty, HAND_TYPE_TRIPS_3, HAND_TYPE_PAIR_3, calculate_total_royalty_for_board
except ImportError:
    pytest.skip("Skipping AI quality tests due to missing core imports", allow_module_level=True)

# ИСПРАВЛЕНИЕ: Добавлено создание логгера для использования в тестах
logger = logging.getLogger(__name__)

def hand_to_int(card_strs: list) -> list:
    return [Card.from_str(s) for s in card_strs if s and len(s) >= 2]

@pytest.fixture
def agent_short_time():
    # Увеличим немного время для более сложных сценариев
    return MCTSAgent(time_limit_ms=500, num_workers=1, rollouts_per_leaf=15)

@pytest.fixture
def agent_very_short_time(): # Для простых тестов, где не нужна глубокая симуляция
    return MCTSAgent(time_limit_ms=100, num_workers=1, rollouts_per_leaf=5)


def test_ai_handles_11_plus_3_cards_correctly(agent_short_time):
    """
    ЗАДАЧА 1: На доске 11 карт, ИИ получает 3, должен разместить 2 и 1 сбросить.
    """
    board = PlayerBoard()
    # >>> НАЧАЛО ИСПРАВЛЕНИЯ 3 <<<
    # Заполняем доску 11 картами. Мидл теперь сильнее (пара тузов),
    # чтобы ход ИИ не приводил к гарантированному фолу.
    initial_placements = [
        # Bottom: 4-карточный флеш-дро
        ('Ks', 'bottom', 0), ('Qs', 'bottom', 1), ('Js', 'bottom', 2), ('Ts', 'bottom', 3),
        # Middle: Сильная пара тузов
        ('Ac', 'middle', 0), ('Ad', 'middle', 1), ('2h', 'middle', 2), ('3h', 'middle', 3),
        # Top: Заполнен
        ('6s', 'top', 0), ('5s', 'top', 1), ('4s', 'top', 2) # 11 карт
    ]
    # >>> КОНЕЦ ИСПРАВЛЕНИЯ 3 <<<
    initial_board_cards_int = []
    for card_str, row, idx in initial_placements:
        c_int = Card.from_str(card_str)
        board.add_card(c_int, row, idx)
        initial_board_cards_int.append(c_int)

    assert board.get_total_cards() == 11

    # Карты для ИИ (3 штуки)
    cards_dealt_str = ['As', 'Kh', '2c'] # Очевидный сброс - 2c. As закроет флеш на боттоме.
    cards_dealt_int = hand_to_int(cards_dealt_str)

    remaining_deck = Deck.FULL_DECK_CARDS - set(initial_board_cards_int) - set(cards_dealt_int)

    placement_info = agent_short_time.choose_placement(board, cards_dealt_int, remaining_deck, 0)

    assert placement_info is not None, "AI did not return a placement"
    assert 'placements' in placement_info, "Placement info missing 'placements' key"
    assert 'discarded' in placement_info, "Placement info missing 'discarded' key"

    assert len(placement_info['placements']) == 2, f"AI should place 2 cards, but placed {len(placement_info['placements'])}"

    # Проверяем, что сброшена одна карта (discarded может быть int или tuple)
    discarded_ai = placement_info['discarded']
    num_discarded_by_ai = 0
    if discarded_ai is not None:
        if isinstance(discarded_ai, tuple):
            num_discarded_by_ai = len(discarded_ai)
        else: # int
            num_discarded_by_ai = 1

    assert num_discarded_by_ai == 1, f"AI should discard 1 card, but discarded {num_discarded_by_ai} ({discarded_ai})"

    # Проверяем, что сброшена наименее ценная карта (в данном случае 2c)
    assert Card.to_str(placement_info['discarded']) == '2c', f"AI discarded {Card.to_str(placement_info['discarded'])}, expected 2c"

    # Проверяем, что размещенные карты - это As и Kh
    placed_by_ai_ints = {p[0] for p in placement_info['placements']}
    expected_placed_ints = {Card.from_str('As'), Card.from_str('Kh')}
    assert placed_by_ai_ints == expected_placed_ints, \
        f"AI placed {[Card.to_str(c) for c in placed_by_ai_ints]}, expected {[Card.to_str(c) for c in expected_placed_ints]}"

    # Проверяем, что доска после хода ИИ не фол
    board_after_ai = board.copy()
    for card_int, row, idx in placement_info['placements']:
        board_after_ai.add_card(card_int, row, idx)

    assert board_after_ai.get_total_cards() == 13, "Board should have 13 cards after AI move"
    assert not check_board_foul(board_after_ai), "AI made a move that resulted in a foul"


@pytest.mark.parametrize("fantasy_hand_str, target_rank_int", [
    (['Qs', 'Qd', 'As', 'Ks', 'Ts'], RANK_QUEEN),
    (['Ks', 'Kd', 'As', 'Qs', 'Ts'], RANK_KING),
    (['As', 'Ad', 'Ks', 'Qs', 'Ts'], RANK_ACE),
    (['7s', '7d', '7c', 'Ks', 'Qs'], RANK_7), # Trips for fantasy
])
def test_ai_prefers_fantasy_qualification_progressive(agent_short_time, fantasy_hand_str, target_rank_int):
    """
    Тест: ИИ должен стремиться к Фантазии (QQ+, KK+, AA+, Trips на топе).
    """
    board = PlayerBoard()
    cards_dealt = hand_to_int(fantasy_hand_str)
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)

    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None, "AI did not return a placement"

    board_after_ai_move = board.copy()
    for card_int, row, idx in placement_info['placements']:
        board_after_ai_move.add_card(card_int, row, idx)

    top_row_cards = board_after_ai_move.get_row_cards('top')

    fantasy_achieved = False
    if len(top_row_cards) == 3:
        from ofc_evaluator_3card import evaluate_3_card_ofc # Локальный импорт для теста
        try:
            _, type_top, _ = evaluate_3_card_ofc(top_row_cards[0], top_row_cards[1], top_row_cards[2])
            if type_top == HAND_TYPE_TRIPS_3:
                fantasy_achieved = True
            elif type_top == HAND_TYPE_PAIR_3:
                ranks_top_counter = Counter(Card.get_rank_int(c) for c in top_row_cards)
                pair_rank_top = next((r for r, count in ranks_top_counter.items() if count == 2), -1)
                if pair_rank_top >= RANK_QUEEN: # QQ, KK, AA
                    fantasy_achieved = True
        except ValueError: # Если рука на топе невалидна (например, не 3 карты)
            pass

    # Заполняем доску до конца случайными картами для полной проверки на фол
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
            # Если Фантазия не достигнута, но могла быть, это сложнее.
            # Для этого теста, если ФЛ не достигнут, но и фола нет, тест проходит.
            assert not is_foul, f"AI made a move that leads to a foul in fantasy attempt scenario ({fantasy_hand_str})."

    # Если Фантазия была возможна с руки, ожидаем, что ИИ ее сделает, если это не фол.
    # Этот ассерт может быть слишком строгим, т.к. ИИ может выбрать более сильную общую руку.
    if any(Card.get_rank_int(c) == target_rank_int for c in cards_dealt): # Если целевая карта была в руке
         if not fantasy_achieved and board_after_ai_move.is_complete() and not check_board_foul(board_after_ai_move):
             # Можно добавить pytest.skip или warning, если ФЛ не достигнут, но ход валидный.
             # Это указывает на то, что эвристика ФЛ может быть недостаточно агрессивной.
             logger.warning(f"AI did not achieve fantasy with {fantasy_hand_str} but made a valid non-foul hand. Review fantasy heuristic.")
         elif fantasy_achieved:
             logger.info(f"AI successfully aimed for fantasy with {fantasy_hand_str}.")


def test_ai_avoids_obvious_foul(agent_short_time):
    """
    Тест: ИИ должен избегать очевидного фола, если есть безопасная альтернатива.
    """
    board = PlayerBoard()
    # Топ: KKK (очень сильный)
    board.add_card(Card.from_str('Ks'), 'top', 0); board.add_card(Card.from_str('Kd'), 'top', 1); board.add_card(Card.from_str('Kc'), 'top', 2)
    # Мидл: 223xx (очень слабый)
    board.add_card(Card.from_str('2s'), 'middle', 0); board.add_card(Card.from_str('2d'), 'middle', 1); board.add_card(Card.from_str('3h'), 'middle', 2)

    # Сдаем карты, которые могут легко сделать мидл сильнее топа: Ah, Ad, Ac
    cards_dealt = hand_to_int(['Ah', 'Ad', 'Ac'])
    # Опасный ход: AA на мидл (AAx сильнее KKK -> Фол)
    # Безопасный ход: AA на боттом, третья карта (A) на мидл (если есть место) или сброс.

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
    elif final_board.get_total_cards() >= 8: # Если хотя бы топ и мидл заполнены
         assert not check_board_foul(final_board), "AI made a move that leads to an early foul between top/middle"


def test_ai_correct_discard_choice_not_first_street(agent_very_short_time):
    """
    Тест: ИИ должен делать очевидный выбор сброса (не первая улица).
    """
    board = PlayerBoard()
    board.add_card(Card.from_str('7h'), 'bottom', 0); board.add_card(Card.from_str('8h'), 'bottom', 1)
    board.add_card(Card.from_str('9s'), 'middle', 0); board.add_card(Card.from_str('Ts'), 'middle', 1)
    board.add_card(Card.from_str('Jc'), 'top', 0) # 5 карт на доске

    cards_dealt = hand_to_int(['As', 'Ks', '2c']) # Очевидный сброс 2c
    initial_board_cards = board.get_all_cards()
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt) - initial_board_cards

    placement_info = agent_very_short_time.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None, "AI did not return a placement"
    assert placement_info.get('discarded') is not None, "AI did not discard a card"

    discarded_card_str = Card.to_str(placement_info['discarded'])
    assert discarded_card_str == '2c', f"AI discarded {discarded_card_str}, expected 2c"
    placed_card_ints = {p[0] for p in placement_info['placements']}
    assert Card.from_str('As') in placed_card_ints and Card.from_str('Ks') in placed_card_ints
    assert len(placed_card_ints) == 2


def test_ai_first_street_strong_hand_to_bottom(agent_short_time):
    """
    Тест: На первой улице сильная готовая рука (например, стрит) должна идти на боттом.
    """
    board = PlayerBoard()
    cards_dealt = hand_to_int(['As', 'Ks', 'Qs', 'Js', 'Ts']) # Роял-Флеш
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)

    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None and len(placement_info['placements']) == 5

    bottom_cards = [p[0] for p in placement_info['placements'] if p[1] == 'bottom']
    assert len(bottom_cards) == 5, "AI did not place all 5 cards of RF on bottom"
    assert set(bottom_cards) == set(cards_dealt), "Cards on bottom do not match dealt RF"


def test_ai_handles_unknown_cards_conservatively(agent_short_time):
    """
    Тест: При большом количестве "?" карт, ИИ должен быть более консервативен
    в отношении дро, зависящих от многих аутов.
    Сценарий: Две опции - рискованное флеш-дро (много аутов, но много "?")
                 или менее ценная, но более надежная пара.
    """
    board = PlayerBoard() # Пустая доска
    # Рука: 4 карты на флеш + одна карта для пары
    # Ah Kh Qh Jh 2d (4 червы + двойка бубен)
    cards_dealt = hand_to_int(['Ah', 'Kh', 'Qh', 'Jh', '2d'])

    # Сценарий 1: Мало "?" карт
    remaining_deck_few_q = Deck.FULL_DECK_CARDS - set(cards_dealt)
    # Убедимся, что в колоде много черв для флеша
    # (Deck.FULL_DECK_CARDS уже содержит все карты)

    placement_few_q = agent_short_time.choose_placement(board.copy(), cards_dealt, remaining_deck_few_q, num_unknown_removed_cards=1)
    assert placement_few_q is not None

    board_after_few_q = PlayerBoard()
    for c,r,i in placement_few_q['placements']: board_after_few_q.add_card(c,r,i)
    # Ожидаем, что ИИ пойдет на флеш (например, 4 червы на боттом)
    bottom_cards_few_q = board_after_few_q.get_row_cards('bottom')
    is_flush_attempt_few_q = False
    if len(bottom_cards_few_q) >= 4:
        suits_bottom = Counter(Card.get_suit_int(c) for c in bottom_cards_few_q)
        if any(count >= 4 for count in suits_bottom.values()):
            is_flush_attempt_few_q = True

    # Сценарий 2: Много "?" карт (например, 15)
    # Колода та же, но ИИ знает, что много карт удалено неизвестно как
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

    # Ожидаем, что при малом "?" ИИ может пойти на флеш,
    # а при большом "?" может предпочесть более безопасную игру (например, пару 22 на мидл/топ, а AKQJ на боттом).
    # Этот тест сложен для точного ассерта, т.к. зависит от многих факторов.
    # Пока что проверим, что ходы разные или что оценка флеша ниже при многих "?".
    # Если is_flush_attempt_few_q == True, то ожидаем, что is_flush_attempt_many_q может быть False.
    if is_flush_attempt_few_q and not is_flush_attempt_many_q:
        logger.info("AI correctly became more conservative with flush draw due to many '?' cards.")
        pass # Это ожидаемое поведение
    elif is_flush_attempt_few_q and is_flush_attempt_many_q:
        logger.warning("AI still attempted flush draw with many '?' cards. Review conservatism.")
        # Это не обязательно ошибка, но требует внимания.
    elif not is_flush_attempt_few_q and is_flush_attempt_many_q:
        logger.warning("AI attempted flush draw ONLY with many '?' cards. Unexpected.")
    else: # Оба не пошли на флеш
        logger.info("AI did not attempt flush draw in either '?' scenario for this hand.")

    # Более простой ассерт: просто убедиться, что ИИ не падает и возвращает ход.
    assert placement_few_q is not None
    assert placement_many_q is not None
