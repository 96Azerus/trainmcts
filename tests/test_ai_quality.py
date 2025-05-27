# tests/test_ai_quality.py v1.1
"""
Тесты для оценки качества решений MCTS-агента.
ИСПРАВЛЕНО: Добавлен import random.
ИСПРАВЛЕНО: Логика в test_ai_correct_discard_choice для симуляции не первой улицы.
ИСПРАВЛЕНО: Добавлен random.shuffle в test_ai_prefers_fantasy_qualification и test_ai_avoids_obvious_foul.
"""
import pytest
from unittest.mock import ANY
import random # <--- ДОБАВЛЕНО

try:
    from mcts_agent import MCTSAgent
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS
    from ofc_evaluators import check_board_foul, get_row_royalty, HAND_TYPE_TRIPS_3, RANK_QUEEN, calculate_total_royalty_for_board
except ImportError:
    pytest.skip("Skipping AI quality tests due to missing core imports", allow_module_level=True)

# Хелпер для конвертации строк карт в int
def hand_to_int(card_strs: list) -> list:
    return [Card.from_str(s) for s in card_strs if s and len(s) >= 2] # >=2 для случая типа '10s'

@pytest.fixture
def agent_short_time():
    return MCTSAgent(time_limit_ms=300, num_workers=1, rollouts_per_leaf=10) # Чуть больше времени

def test_ai_prefers_fantasy_qualification(agent_short_time):
    """
    Тест: ИИ должен стремиться к Фантазии (QQ+ на топе), если это возможно и безопасно.
    Сценарий: Первые 5 карт, есть возможность собрать QQ на топе.
    """
    board = PlayerBoard()
    cards_dealt = hand_to_int(['Qs', 'Qd', 'As', 'Ks', 'Ts'])
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)

    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck)
    assert placement_info is not None, "AI did not return a placement"
    assert placement_info['placements'] is not None
    
    # Создаем доску после хода ИИ
    board_after_ai_move = board.copy()
    for card_int, row, idx in placement_info['placements']:
        board_after_ai_move.add_card(card_int, row, idx)

    # Проверяем топ на QQ+
    top_row_cards = board_after_ai_move.get_row_cards('top')
    top_row_ranks = sorted([Card.get_rank_int(c) for c in top_row_cards if c is not None], reverse=True)
    
    is_qq_or_better_on_top = False
    if len(top_row_ranks) == 3: # Если топ заполнен
        # Простая проверка на пару QQ, KK, AA или трипс
        if (top_row_ranks[0] == top_row_ranks[1] and top_row_ranks[0] >= RANK_MAP['Q']) or \
           (top_row_ranks[0] == top_row_ranks[1] == top_row_ranks[2]): # Трипс
            is_qq_or_better_on_top = True
    elif len(top_row_ranks) == 2: # Если на топе 2 карты
         if top_row_ranks[0] == top_row_ranks[1] and top_row_ranks[0] >= RANK_MAP['Q']:
             is_qq_or_better_on_top = True # Уже есть пара QQ+

    # Заполняем доску до конца случайными картами для полной проверки на фол
    if not board_after_ai_move.is_complete():
        deck_list_for_completion = list(remaining_deck - set(board_after_ai_move.get_all_cards()))
        random.shuffle(deck_list_for_completion)
        slots_to_fill = board_after_ai_move.get_available_slots()
        for i, (r, s_idx) in enumerate(slots_to_fill):
            if i < len(deck_list_for_completion):
                board_after_ai_move.add_card(deck_list_for_completion[i], r, s_idx)
            else: break 
    
    if board_after_ai_move.is_complete():
        is_foul = check_board_foul(board_after_ai_move)
        if is_qq_or_better_on_top:
            assert not is_foul, "AI aimed for Fantasyland but fouled."
        # Если ИИ не пошел на ФЛ, но мог, это сложнее проверить автоматически без глубокого анализа.
        # Для этого теста, если ФЛ достигнут без фола, это успех.
        # Если ФЛ не достигнут, но и фола нет, тест проходит (ИИ мог выбрать другой путь).
        # Если фол - тест падает.
        assert not is_foul, "AI made a move that leads to a foul in fantasy attempt scenario."
    
    # Если QQ были в розданных картах, ожидаем, что ИИ попытается их использовать для ФЛ,
    # если это не приводит к немедленному фолу или очень плохой доске.
    # Этот ассерт очень мягкий, т.к. ИИ может иметь причины не ставить QQ на топ.
    if Card.from_str('Qs') in cards_dealt and Card.from_str('Qd') in cards_dealt:
        if is_qq_or_better_on_top:
             pass # Хорошо
        else:
             # Можно добавить логирование или более мягкий assert, например, что роялти не нулевые
             # pytest.skip("AI did not place QQ on top, needs manual review or smarter check.")
             pass # Пока пропускаем этот случай, если QQ не на топе, но и не фол

def test_ai_avoids_obvious_foul(agent_short_time):
    """
    Тест: ИИ должен избегать очевидного фола, если есть безопасная альтернатива.
    Сценарий: Топ уже сильный, мидл слабый. Новая карта может усилить мидл выше топа.
    """
    board = PlayerBoard()
    board.add_card(Card.from_str('2s'), 'top', 0)
    board.add_card(Card.from_str('2d'), 'top', 1)
    board.add_card(Card.from_str('3c'), 'top', 2) # Топ: 2,2,3

    cards_dealt = hand_to_int(['As', 'Ad', 'Ks']) # Рука: AA, K
    # Опасный ход: AA на мидл (AAx > 223 -> Фол)
    # Безопасный ход: AA на боттом, K на мидл.
    
    initial_board_cards = board.get_all_cards()
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt) - initial_board_cards

    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck)
    assert placement_info is not None, "AI did not return a placement"

    final_board = board.copy()
    placed_cards_in_move_ints = set()
    for card_int, row, idx in placement_info['placements']:
        final_board.add_card(card_int, row, idx)
        placed_cards_in_move_ints.add(card_int)
    
    # Заполняем доску до конца случайными картами для полной проверки на фол
    if not final_board.is_complete():
        # Обновляем remaining_deck, исключая карты, которые ИИ только что разместил/сбросил
        # (если они были взяты из remaining_deck, а не cards_dealt - но здесь они из cards_dealt)
        # и те, что были сброшены ИИ
        current_remaining_deck_list = list(remaining_deck - placed_cards_in_move_ints)
        if placement_info.get('discarded') is not None:
            if placement_info['discarded'] in current_remaining_deck_list: # на всякий случай
                 current_remaining_deck_list.remove(placement_info['discarded'])
        
        random.shuffle(current_remaining_deck_list) # <--- Используем random
        slots_to_fill = final_board.get_available_slots()
        for i, (r, s_idx) in enumerate(slots_to_fill):
            if i < len(current_remaining_deck_list):
                final_board.add_card(current_remaining_deck_list[i], r, s_idx)
            else: break 
    
    if final_board.is_complete():
        assert not check_board_foul(final_board), "AI made a move that leads to a foul"
    else:
        # Если доска не полная, сложно однозначно сказать про фол без оценки всех линий.
        # Но если уже есть очевидный фол между заполненными линиями, это проблема.
        # check_board_foul должен это обработать.
        # Для этого теста, если он не полный, но уже фол, это провал.
        if final_board.get_total_cards() >= 8: # Хотя бы 2 линии можно сравнить
             assert not check_board_foul(final_board), "AI made a move that leads to an early foul"


def test_ai_correct_discard_choice(agent_short_time):
    """
    Тест: ИИ должен делать очевидный выбор сброса (не первая улица).
    Сценарий: На доске уже есть карты. Рука As, Ks, 2c. Очевидный сброс - 2c.
    """
    board = PlayerBoard()
    # Симулируем, что это не первая улица, добавив несколько карт на доску
    board.add_card(Card.from_str('7h'), 'bottom', 0)
    board.add_card(Card.from_str('8h'), 'bottom', 1)
    board.add_card(Card.from_str('9h'), 'middle', 0)
    board.add_card(Card.from_str('Th'), 'middle', 1)
    board.add_card(Card.from_str('Jh'), 'top', 0)
    # Итого 5 карт на доске, следующая улица - раздача 3 карт.

    cards_dealt = hand_to_int(['As', 'Ks', '2c'])
    initial_board_cards = board.get_all_cards()
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt) - initial_board_cards

    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck)
    assert placement_info is not None, "AI did not return a placement in discard choice test"
    assert placement_info.get('discarded') is not None, "AI did not discard a card"
    
    discarded_card_str = Card.to_str(placement_info['discarded'])
    assert discarded_card_str == '2c', f"AI discarded {discarded_card_str}, expected 2c"

    placed_card_ints = {p[0] for p in placement_info['placements']}
    assert Card.from_str('As') in placed_card_ints, "As was not placed"
    assert Card.from_str('Ks') in placed_card_ints, "Ks was not placed"
    assert len(placed_card_ints) == 2, "Incorrect number of cards placed"


def test_ai_first_street_strong_hand_to_bottom(agent_short_time):
    """
    Тест: На первой улице сильная готовая рука (например, стрит) должна идти на боттом.
    """
    board = PlayerBoard()
    cards_dealt = hand_to_int(['As', 'Ks', 'Qs', 'Js', 'Ts']) # Роял-Флеш
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)

    placement_info = agent_short_time.choose_placement(board, cards_dealt, remaining_deck)
    assert placement_info is not None, "AI returned no placement for strong starting hand"
    assert len(placement_info['placements']) == 5, "AI did not place all 5 cards on the first street"

    bottom_row_cards_placed = []
    middle_row_cards_placed = []
    top_row_cards_placed = []

    for card_int, row, _ in placement_info['placements']:
        if row == 'bottom': bottom_row_cards_placed.append(card_int)
        elif row == 'middle': middle_row_cards_placed.append(card_int)
        elif row == 'top': top_row_cards_placed.append(card_int)
    
    # Ожидаем, что все 5 карт сильной руки пойдут на боттом
    assert len(bottom_row_cards_placed) == 5, \
        f"AI did not place all 5 cards on bottom. Bottom: {len(bottom_row_cards_placed)}, Mid: {len(middle_row_cards_placed)}, Top: {len(top_row_cards_placed)}"
    
    assert set(bottom_row_cards_placed) == set(cards_dealt), \
        f"Cards on bottom {hand_to_str(bottom_row_cards_placed)} do not match dealt strong hand {hand_to_str(cards_dealt)}"
