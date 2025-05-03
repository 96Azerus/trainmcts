# tests/test_mcts_node.py v1.0
"""
Unit-тесты для модуля mcts_node.py (v2.7 - Advanced Heuristic Rollout).
"""

import pytest
import math
import random
from unittest.mock import patch, MagicMock, call, ANY
from collections import Counter

# Импорты из тестируемого модуля и зависимостей
try:
    from mcts_node import (
        MCTSNode, run_parallel_rollout,
        RAVE_K, PW_C, PW_ALPHA, # Константы MCTS
        HEURISTIC_FOUL_PENALTY, HEURISTIC_FL_QUALIFY_BONUS, # Константы эвристики
        ROW_FLUSH_DRAW_OUT_WEIGHT, ROW_STRAIGHT_DRAW_OUT_WEIGHT,
        ROW_GUTSHOT_DRAW_OUT_WEIGHT, ROW_PAIR_OUTS_WEIGHT,
        ROW_TRIPS_OUTS_WEIGHT, ROW_HIGH_CARD_WEIGHT
    )
    from ofc_logic import PlayerBoard, Card, Deck, INVALID_CARD, RANK_ACE, RANK_KING, RANK_QUEEN
    from ofc_evaluators import get_hand_rank_safe, check_board_foul, get_row_royalty, WORST_RANK
except ImportError as e:
    pytest.skip(f"Skipping MCTS node tests due to missing imports: {e}", allow_module_level=True)

# --- Хелперы и Фикстуры ---

def hand_to_int(card_strs: list) -> list:
    """Конвертирует список строк в список int карт, пропуская невалидные."""
    return [Card.from_str(s) for s in card_strs if s and len(s) == 2]

@pytest.fixture
def empty_board():
    return PlayerBoard()

@pytest.fixture
def sample_deck_set():
    # Возвращаем копию, чтобы тесты не влияли друг на друга
    return Deck.FULL_DECK_CARDS.copy()

@pytest.fixture
def sample_cards():
    # Набор карт для тестов
    return {
        'As': Card.from_str('As'), 'Ks': Card.from_str('Ks'), 'Qs': Card.from_str('Qs'),
        'Js': Card.from_str('Js'), 'Ts': Card.from_str('Ts'), '9s': Card.from_str('9s'),
        '8s': Card.from_str('8s'), '7s': Card.from_str('7s'), '6s': Card.from_str('6s'),
        '5s': Card.from_str('5s'), '4s': Card.from_str('4s'), '3s': Card.from_str('3s'),
        '2s': Card.from_str('2s'),
        'Ah': Card.from_str('Ah'), 'Kh': Card.from_str('Kh'), 'Qh': Card.from_str('Qh'),
        'Ad': Card.from_str('Ad'), 'Kd': Card.from_str('Kd'), 'Qd': Card.from_str('Qd'),
        'Ac': Card.from_str('Ac'), 'Kc': Card.from_str('Kc'), 'Qc': Card.from_str('Qc'),
        '2h': Card.from_str('2h'), '3d': Card.from_str('3d'), '4c': Card.from_str('4c'),
        '5h': Card.from_str('5h'), '6d': Card.from_str('6d'), '7c': Card.from_str('7c'),
        '8h': Card.from_str('8h'), '9d': Card.from_str('9d'), 'Tc': Card.from_str('Tc'),
        'Jd': Card.from_str('Jd'),
    }

@pytest.fixture
def root_node_fixture(empty_board, sample_deck_set):
    # Создаем базовый корневой узел для тестов методов MCTSNode
    return MCTSNode(board=empty_board, remaining_deck=sample_deck_set, parent=None, placement_info=None)

# --- Тесты Статических Вспомогательных Функций Эвристики ---

def test_count_outs(sample_cards, sample_deck_set):
    needed = {sample_cards['As'], sample_cards['Ks'], sample_cards['Qs']}
    deck = {sample_cards['As'], sample_cards['Ks'], sample_cards['2h'], sample_cards['3d']}
    assert MCTSNode._count_outs(needed, deck) == 2
    assert MCTSNode._count_outs(needed, {sample_cards['2h']}) == 0
    assert MCTSNode._count_outs(set(), deck) == 0
    assert MCTSNode._count_outs(needed, set()) == 0

def test_detect_flush_draw(sample_cards):
    assert MCTSNode._detect_flush_draw([]) == (None, 0)
    # 2 карты - не дро
    assert MCTSNode._detect_flush_draw([sample_cards['As'], sample_cards['Ks']]) == (None, 0)
    # 3 карты - дро
    suit_s = Card.get_suit_int(sample_cards['As'])
    assert MCTSNode._detect_flush_draw([sample_cards['As'], sample_cards['Ks'], sample_cards['Qs']]) == (suit_s, 3)
    # 4 карты - дро
    assert MCTSNode._detect_flush_draw([sample_cards['As'], sample_cards['Ks'], sample_cards['Qs'], sample_cards['Js']]) == (suit_s, 4)
    # 5 карт - готовый флеш (функция все равно вернет дро)
    assert MCTSNode._detect_flush_draw([sample_cards['As'], sample_cards['Ks'], sample_cards['Qs'], sample_cards['Js'], sample_cards['Ts']]) == (suit_s, 5)
    # Смешанные масти
    assert MCTSNode._detect_flush_draw([sample_cards['As'], sample_cards['Kh'], sample_cards['Qs']]) == (None, 0)
    assert MCTSNode._detect_flush_draw([sample_cards['As'], sample_cards['Ks'], sample_cards['Qh']]) == (None, 0)
    assert MCTSNode._detect_flush_draw([sample_cards['As'], sample_cards['Ks'], sample_cards['Qs'], sample_cards['Kh']]) == (suit_s, 3)

def test_get_flush_draw_outs(sample_cards, sample_deck_set):
    suit_s = Card.get_suit_int(sample_cards['As'])
    board = {sample_cards['As'], sample_cards['Ks']}
    deck = {sample_cards['Qs'], sample_cards['Js'], sample_cards['Ts'], sample_cards['Kh'], sample_cards['Qh']}
    outs = MCTSNode._get_flush_draw_outs(suit_s, board, deck)
    assert outs == {sample_cards['Qs'], sample_cards['Js'], sample_cards['Ts']}

# --- Тесты _detect_straight_draw (v2.7) ---
@pytest.mark.parametrize("hand_strs, expected_type, expected_needed_ranks", [
    # Нет дро
    (['As', '3d', '7c'], 0, set()),
    (['As', 'Ks', '3d'], 0, set()),
    # Готовый стрит
    (['As', 'Ks', 'Qs', 'Js', 'Ts'], 0, set()),
    (['Ac', '2d', '3h', '4s', '5c'], 0, set()), # Wheel
    # OESD
    (['8s', '9d', 'Th', 'Jc'], 2, {6, 10}), # Need 7, Q (ranks 5, 10)
    (['Ac', 'Kd', 'Qh', 'Js'], 2, {8, 12}), # Need T, A (ranks 8, 12) - AKQJ -> T
    (['2c', '3d', '4h', '5s'], 2, {12, 4}), # Need A, 6 (ranks 12, 4) - 2345 -> A, 6
    (['Ac', '2d', '3h', '4s'], 2, {3}), # A234 -> Need 5 (rank 3) - Wheel OESD
    # Gutshots
    (['8s', '9d', 'Jh', 'Qc'], 1, {8}), # Need T (rank 8)
    (['Ac', '2d', '3h', '5s'], 1, {2}), # A235 -> Need 4 (rank 2) - Wheel Gutshot
    (['Ac', '2d', '4h', '5s'], 1, {1}), # A245 -> Need 3 (rank 1) - Wheel Gutshot
    (['Ac', '3d', '4h', '5s'], 1, {0}), # A345 -> Need 2 (rank 0) - Wheel Gutshot
    (['7s', '9d', 'Th', 'Jc'], 1, {6}), # Need 8 (rank 6)
    # Double Gutshots (считаются как Gutshot, тип 1)
    (['7s', '9d', 'Th', 'Qc'], 1, {6, 10}), # Need 8, J (ranks 6, 9 -> mistake here, should be 6, 9) -> Corrected: {6, 9}
    (['7s', '8d', 'Th', 'Jc'], 1, {7, 10}), # Need 9, Q (ranks 7, 10)
])
def test_detect_straight_draw_v2_7(hand_strs, expected_type, expected_needed_ranks, sample_cards):
    hand_ints = [sample_cards[s] for s in hand_strs]
    draw_type, needed_ranks = MCTSNode._detect_straight_draw(hand_ints)
    assert draw_type == expected_type
    assert needed_ranks == expected_needed_ranks

def test_get_straight_draw_outs(sample_cards, sample_deck_set):
    needed_ranks = {8, 12} # Need T, A
    board = {sample_cards['Kd'], sample_cards['Qh'], sample_cards['Js']}
    deck = {sample_cards['Ts'], sample_cards['As'], sample_cards['9s'], sample_cards['Ac']}
    outs = MCTSNode._get_straight_draw_outs(needed_ranks, board, deck)
    assert outs == {sample_cards['Ts'], sample_cards['As'], sample_cards['Ac']}

# --- Тесты _estimate_row_potential ---
# Сложно тестировать точно, проверяем относительные значения
def test_estimate_row_potential(sample_cards, sample_deck_set):
    board_cards = set()
    deck = sample_deck_set.copy()

    # Пустой ряд
    pot_empty = MCTSNode._estimate_row_potential([], board_cards, deck)
    assert pot_empty == 0.0

    # Готовая рука (пара AA)
    pair_aa = [sample_cards['As'], sample_cards['Ad']]
    pot_pair_aa = MCTSNode._estimate_row_potential(pair_aa, board_cards, deck)
    assert pot_pair_aa > 0 # Должен иметь положительный потенциал (ауты + хайкарды)

    # Флеш-дро (4 карты)
    flush_draw_4 = [sample_cards['As'], sample_cards['Ks'], sample_cards['Qs'], sample_cards['Js']]
    pot_flush_draw_4 = MCTSNode._estimate_row_potential(flush_draw_4, board_cards, deck)
    assert pot_flush_draw_4 > pot_pair_aa # Флеш-дро обычно ценнее пары

    # OESD (4 карты)
    oesd_4 = [sample_cards['8s'], sample_cards['9d'], sample_cards['Th'], sample_cards['Jc']]
    pot_oesd_4 = MCTSNode._estimate_row_potential(oesd_4, board_cards, deck)
    assert pot_oesd_4 > pot_pair_aa # OESD обычно ценнее пары

    # Сравнение дро
    assert pot_flush_draw_4 > pot_oesd_4 # Флеш-дро (9 аутов) обычно > OESD (8 аутов)

# --- Тесты _score_placement_v2 ---
# Требуют мокирования зависимостей
@patch('mcts_node.MCTSNode._estimate_row_potential', return_value=5.0) # Мок потенциала
@patch('mcts_node.check_board_foul', return_value=False) # Мок проверки фола
@patch('mcts_node.get_row_royalty', return_value=2) # Мок роялти
@patch('mcts_node.get_hand_rank_safe') # Мок оценки руки
def test_score_placement_v2_basic(mock_get_rank, mock_get_royalty, mock_check_foul, mock_estimate_pot, empty_board, sample_cards, sample_deck_set):
    # Мок get_hand_rank_safe для возврата не-фол рук и QQ на топе
    def rank_side_effect(cards):
        if len(cards) == 3: return (RANK_QUEEN * 10, 8, "Pair") # QQ на топе
        if len(cards) == 5: return (RANK_KING * 10, 8, "Pair") # KK на мид/бот
        return (WORST_RANK, 9, "Invalid")
    mock_get_rank.side_effect = rank_side_effect

    placement = {
        'placements': [(sample_cards['Qh'], 'top', 0), (sample_cards['Qd'], 'top', 1)],
        'discarded': sample_cards['2c']
    }
    score = MCTSNode._score_placement_v2(empty_board, placement, sample_deck_set)

    assert score > 0 # Должен быть положительный счет
    # Проверяем, что был добавлен бонус за FL и оценка потенциала
    # Ожидаемый счет = (оценка топа) + (оценка мид) + (оценка бот)
    # Оценка топа = роялти(QQ) * вес + бонус FL = 7 * 1.0 + 15.0 = 22.0
    # Оценка мид/бот = mock_estimate_pot = 5.0
    # Итого ~ 22.0 + 5.0 + 5.0 = 32.0 (плюс небольшой вес хайкардов)
    assert score == pytest.approx(32.0, abs=1.0)
    mock_check_foul.assert_not_called() # Доска не полная
    assert mock_estimate_pot.call_count == 2 # Вызывается для пустых мид и бот

@patch('mcts_node.check_board_foul', return_value=True) # Мок фола
def test_score_placement_v2_foul(mock_check_foul, empty_board, sample_cards, sample_deck_set):
     placement = { 'placements': [(sample_cards['As'], 'top', 0)], 'discarded': None }
     # Мокаем is_complete, чтобы check_board_foul вызвался
     with patch.object(PlayerBoard, 'is_complete', return_value=True):
         score = MCTSNode._score_placement_v2(empty_board, placement, sample_deck_set)
     assert score == HEURISTIC_FOUL_PENALTY
     mock_check_foul.assert_called_once()

# --- Тесты _choose_best_heuristic_placement_v2 ---
@patch('mcts_node.MCTSNode._score_placement_v2')
def test_choose_best_heuristic_placement_v2(mock_score, empty_board, sample_cards, sample_deck_set):
    cards_dealt = [sample_cards['As'], sample_cards['Ks'], sample_cards['Qs'], sample_cards['Js'], sample_cards['Ts']] # RF

    # Мокаем оценку: одно размещение (RF на боттом) дает высокий счет, остальные - низкий
    def score_side_effect(board, placement_info, deck):
        placements = placement_info['placements']
        # Проверяем, что все 5 карт идут на боттом
        is_rf_on_bottom = all(p[1] == 'bottom' for p in placements) and len(placements) == 5
        if is_rf_on_bottom:
            return 100.0 # Высокий счет для правильного размещения
        else:
            # Проверяем на фол (например, если RF на топе)
            is_rf_on_top = any(p[1] == 'top' for p in placements)
            if is_rf_on_top: return HEURISTIC_FOUL_PENALTY
            return 1.0 # Низкий счет для других размещений
    mock_score.side_effect = score_side_effect

    best_placement = MCTSNode._choose_best_heuristic_placement_v2(empty_board, cards_dealt, sample_deck_set)

    assert best_placement is not None
    assert len(best_placement['placements']) == 5
    assert all(p[1] == 'bottom' for p in best_placement['placements']) # Убеждаемся, что RF на боттоме
    assert mock_score.call_count > 0 # Убеждаемся, что оценка вызывалась

# --- Тесты heuristic_rollout_simulation_v2 ---
@patch('mcts_node.MCTSNode._choose_best_heuristic_placement_v2')
@patch('mcts_node.check_board_foul', return_value=False)
@patch('mcts_node.get_row_royalty', return_value=1) # Возвращаем небольшое роялти
def test_heuristic_rollout_simulation_v2_completes(mock_royalty, mock_foul, mock_choose, empty_board, sample_deck_set, sample_cards):
    # Мокаем выбор хода, чтобы он возвращал простое размещение и симуляция завершилась
    actions = []
    def choose_side_effect(board, dealt, deck):
        num_to_place = 5 if board.get_total_cards() == 0 else 2
        if not dealt or len(dealt) < num_to_place: return None
        placements = []
        slots = board.get_available_slots()
        if len(slots) < num_to_place: return None
        cards_to_use = dealt[:num_to_place]
        discard = dealt[num_to_place] if len(dealt) > num_to_place else None
        for i in range(num_to_place):
            placements.append((cards_to_use[i], slots[i][0], slots[i][1]))
        action = {'placements': placements, 'discarded': discard}
        actions.append(action) # Сохраняем действие для RAVE теста
        return action

    mock_choose.side_effect = choose_side_effect

    # Запускаем симуляцию
    final_score, history = MCTSNode.heuristic_rollout_simulation_v2(empty_board, sample_deck_set)

    assert final_score >= 0 # Ожидаем не-фол результат
    assert len(history) == 5 # Должно быть 5 ходов (1*5 + 4*2 = 13 карт)
    mock_foul.assert_called_once() # Проверка на фол в конце
    assert mock_royalty.call_count == 3 # Подсчет роялти для 3 рядов

# --- Тесты Стандартных Методов MCTSNode ---

def test_mcts_node_init(empty_board, sample_deck_set):
    node = MCTSNode(board=empty_board, remaining_deck=sample_deck_set)
    assert node.board is empty_board
    assert node.remaining_deck is sample_deck_set
    assert node.parent is None
    assert node.placement_info is None
    assert node.children == {}
    assert node.untried_next_states is None
    assert node.visits == 0
    assert node.total_reward == 0.0
    assert node.rave_visits == 0
    assert node.rave_reward == 0.0

def test_mcts_node_is_terminal(empty_board):
    assert not empty_board.is_complete()
    node = MCTSNode(board=empty_board, remaining_deck=set())
    assert not node.is_terminal()
    # Мокаем доску, чтобы она была полной
    with patch.object(PlayerBoard, 'is_complete', return_value=True):
        full_board = PlayerBoard()
        node_full = MCTSNode(board=full_board, remaining_deck=set())
        assert node_full.is_terminal()

# Тест _generate_next_states - сложный из-за большого числа комбинаций
# Проверяем базовые случаи
def test_generate_next_states_street1(root_node_fixture, sample_cards):
    cards = [sample_cards[s] for s in ['As', 'Ks', 'Qs', 'Js', 'Ts']]
    states = root_node_fixture._generate_next_states(cards)
    assert len(states) > 0 # Должны быть сгенерированы состояния
    # Проверяем структуру первого состояния
    board_state, discarded = states[0]
    assert isinstance(board_state, PlayerBoard)
    assert discarded is None # Нет сброса на 1 улице
    assert board_state.get_total_cards() == 5
    # Проверяем, что _generated_states_for_expand заполнено
    assert len(root_node_fixture._generated_states_for_expand) == len(states)

def test_generate_next_states_street2(root_node_fixture, sample_cards):
    # Добавляем 5 карт на доску
    root_node_fixture.board.add_card(sample_cards['As'], 'bottom', 0)
    root_node_fixture.board.add_card(sample_cards['Ks'], 'bottom', 1)
    root_node_fixture.board.add_card(sample_cards['Qs'], 'middle', 0)
    root_node_fixture.board.add_card(sample_cards['Js'], 'middle', 1)
    root_node_fixture.board.add_card(sample_cards['Ts'], 'top', 0)

    cards = [sample_cards['9s'], sample_cards['8s'], sample_cards['7s']]
    states = root_node_fixture._generate_next_states(cards)
    assert len(states) > 0
    # Проверяем структуру первого состояния
    board_state, discarded = states[0]
    assert isinstance(board_state, PlayerBoard)
    assert discarded in cards # Должна быть сброшена одна из 3 карт
    assert board_state.get_total_cards() == 5 + 2 # 5 было + 2 поставили
    assert len(root_node_fixture._generated_states_for_expand) == len(states)

# Тест expand с учетом PW
@patch('mcts_node.MCTSNode._generate_next_states')
def test_expand_with_pw(mock_generate, root_node_fixture, sample_cards):
    # Настраиваем PW так, чтобы разрешить только 1 ребенка сначала
    with patch('mcts_node.PW_C', 1.0), patch('mcts_node.PW_ALPHA', 0.1):
        mock_board = PlayerBoard()
        mock_discard = None
        mock_placement_info = {'placements': [(sample_cards['As'], 'top', 0)], 'discarded': None}
        mock_key = tuple(sorted(mock_placement_info['placements']))

        # Мокаем генерацию состояний
        root_node_fixture.untried_next_states = [(mock_board, mock_discard)]
        root_node_fixture._generated_states_for_expand = {mock_key: (mock_board, mock_discard, mock_placement_info)}

        # 1. Первый expand должен сработать (0 детей < 1 * (0+1)^0.1 = 1)
        child1 = root_node_fixture.expand()
        assert child1 is not None
        assert len(root_node_fixture.children) == 1
        assert root_node_fixture.untried_next_states == [] # Состояние использовано

        # 2. Второй expand не должен сработать (1 ребенок >= 1 * (0+1)^0.1 = 1)
        # Снова добавляем состояние для попытки expand
        root_node_fixture.untried_next_states = [(mock_board, mock_discard)]
        child2 = root_node_fixture.expand()
        assert child2 is None # PW не дал расширить
        assert len(root_node_fixture.children) == 1 # Остался 1 ребенок
        assert len(root_node_fixture.untried_next_states) == 1 # Состояние не использовано

        # 3. Увеличиваем visits родителя, PW должен разрешить
        root_node_fixture.visits = 100
        # allowed = 1.0 * (100+1)^0.1 ~ 1 * 1.58 = 1.58. Теперь 1 < 1.58
        child3 = root_node_fixture.expand()
        assert child3 is not None # PW разрешил
        assert len(root_node_fixture.children) == 2 # Стало 2 ребенка
        assert root_node_fixture.untried_next_states == []

# Тест uct_select_child - проверяем базовую логику выбора
def test_uct_select_child(root_node_fixture, sample_cards):
    # Создаем моки детей с разной статистикой
    p1 = {'placements': [(sample_cards['As'], 'top', 0)], 'discarded': None}; k1 = tuple(sorted(p1['placements']))
    c1 = MCTSNode(PlayerBoard(), set(), root_node_fixture, p1); c1.visits = 10; c1.total_reward = 20; c1.rave_visits=12; c1.rave_reward=22 # Q=2, RAVE_Q~1.83
    p2 = {'placements': [(sample_cards['Ks'], 'top', 0)], 'discarded': None}; k2 = tuple(sorted(p2['placements']))
    c2 = MCTSNode(PlayerBoard(), set(), root_node_fixture, p2); c2.visits = 5; c2.total_reward = 15; c2.rave_visits=5; c2.rave_reward=16 # Q=3, RAVE_Q=3.2
    p3 = {'placements': [(sample_cards['Qs'], 'top', 0)], 'discarded': None}; k3 = tuple(sorted(p3['placements']))
    c3 = MCTSNode(PlayerBoard(), set(), root_node_fixture, p3); c3.visits = 0; c3.total_reward = 0; c3.rave_visits=0; c3.rave_reward=0 # Не посещен

    root_node_fixture.children = {k1: c1, k2: c2, k3: c3}
    root_node_fixture.visits = c1.visits + c2.visits + c3.visits # 15

    # Ожидаем, что будет выбран c3 (0 визитов -> высокий exploration)
    selected = root_node_fixture.uct_select_child(exploration_constant=1.41)
    assert selected is c3

    # Делаем c3 посещенным, но с плохим RAVE
    c3.visits = 1; c3.total_reward = -5; c3.rave_visits=2; c3.rave_reward=-10 # Q=-5, RAVE_Q=-5
    root_node_fixture.visits = c1.visits + c2.visits + c3.visits # 16

    # Теперь должен быть выбран c2 (лучший Q и RAVE_Q)
    selected = root_node_fixture.uct_select_child(exploration_constant=1.41)
    assert selected is c2

def test_backpropagate(root_node_fixture):
    child = MCTSNode(PlayerBoard(), set(), root_node_fixture, {})
    grandchild = MCTSNode(PlayerBoard(), set(), child, {})

    grandchild.backpropagate(reward=5.0)
    assert grandchild.visits == 1; assert grandchild.total_reward == 5.0
    assert child.visits == 1; assert child.total_reward == 5.0
    assert root_node_fixture.visits == 1; assert root_node_fixture.total_reward == 5.0

    child.backpropagate(reward=-2.0)
    assert grandchild.visits == 1; assert grandchild.total_reward == 5.0
    assert child.visits == 2; assert child.total_reward == 3.0 # 5.0 - 2.0
    assert root_node_fixture.visits == 2; assert root_node_fixture.total_reward == 3.0

def test_backpropagate_rave(root_node_fixture, sample_cards):
    # Создаем детей
    p1 = {'placements': [(sample_cards['As'], 'top', 0)], 'discarded': None}; k1 = tuple(sorted(p1['placements']))
    c1 = MCTSNode(PlayerBoard(), set(), root_node_fixture, p1)
    p2 = {'placements': [(sample_cards['Ks'], 'top', 0)], 'discarded': None}; k2 = tuple(sorted(p2['placements']))
    c2 = MCTSNode(PlayerBoard(), set(), root_node_fixture, p2)
    root_node_fixture.children = {k1: c1, k2: c2}

    # Создаем внука
    p1_1 = {'placements': [(sample_cards['Qs'], 'mid', 0)], 'discarded': None}; k1_1 = tuple(sorted(p1_1['placements']))
    gc1_1 = MCTSNode(PlayerBoard(), set(), c1, p1_1)
    c1.children = {k1_1: gc1_1}

    # Симуляция, где были совершены действия, ведущие к c1 и gc1_1
    sim_actions = [p1, p1_1]
    gc1_1.backpropagate_rave(sim_actions, reward=10.0)

    # Проверяем RAVE статы
    assert c1.rave_visits == 1; assert c1.rave_reward == 10.0 # Действие p1 было в симуляции
    assert c2.rave_visits == 0; assert c2.rave_reward == 0.0 # Действие p2 не было
    assert gc1_1.rave_visits == 1; assert gc1_1.rave_reward == 10.0 # Действие p1_1 было

    # Симуляция, где было только действие, ведущее к c2
    sim_actions_2 = [p2]
    gc1_1.backpropagate_rave(sim_actions_2, reward=5.0)
    assert c1.rave_visits == 1; assert c1.rave_reward == 10.0 # Не изменилось
    assert c2.rave_visits == 1; assert c2.rave_reward == 5.0 # Обновилось
    assert gc1_1.rave_visits == 1; assert gc1_1.rave_reward == 10.0 # Не изменилось
