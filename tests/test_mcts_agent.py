# tests/test_mcts_agent.py v2.2 (Refactored for Set Placement, Mock Fix)
"""
Unit-тесты для модуля mcts_agent.py.
Обновлены для тестирования choose_placement и новой логики MCTSNode.
Исправлено мокирование в test_choose_placement_mcts_loop_simplified.
"""

import pytest
import time
from unittest.mock import patch, MagicMock, PropertyMock, call, ANY

# Импорты из тестируемых модулей и зависимостей
try:
    from mcts_agent import MCTSAgent
    from mcts_node import MCTSNode, run_parallel_rollout
    from ofc_logic import PlayerBoard, Card, Deck, CARD_PLACEHOLDER, RANK_MAP
    from ofc_evaluators import evaluate_3_card_ofc, HAND_TYPE_TRIPS_3
except ImportError:
    pytest.skip("Skipping MCTS agent tests due to missing imports", allow_module_level=True)

# --- Хелперы ---
def hand_to_int(card_strs: list) -> list:
    return Card.hand_to_int(card_strs)

# --- Тесты Инициализации ---
# (Без изменений)
def test_mcts_agent_init_defaults():
    agent = MCTSAgent()
    assert agent.exploration == MCTSAgent.DEFAULT_EXPLORATION
    assert agent.time_limit == MCTSAgent.DEFAULT_TIME_LIMIT_MS / 1000.0
    assert agent.num_workers == MCTSAgent.DEFAULT_NUM_WORKERS
    assert agent.rollouts_per_leaf == MCTSAgent.DEFAULT_ROLLOUTS_PER_LEAF

def test_mcts_agent_init_custom():
    agent = MCTSAgent(exploration=2.0, time_limit_ms=1000, num_workers=2, rollouts_per_leaf=10)
    assert agent.exploration == 2.0
    assert agent.time_limit == 1.0
    assert agent.num_workers == 2
    assert agent.rollouts_per_leaf == 10

# --- Тесты choose_placement ---

@patch('mcts_agent.MCTSNode')
@patch('mcts_agent.multiprocessing.Pool')
def test_choose_placement_basic_run(MockPool, MockMCTSNode):
    """Тестирует базовый запуск choose_placement без ошибок."""
    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = PlayerBoard()
    mock_root.remaining_deck = Deck.FULL_DECK_CARDS.copy()
    mock_root.children = {}
    mock_root.visits = 0; mock_root.total_reward = 0.0
    mock_root.untried_next_states = []
    mock_root._generated_states_for_expand = {}

    card_as = Card.from_str('As'); card_ks = Card.from_str('Ks')
    placement1_info = {'placements': [(card_as, 'top', 0)], 'discarded': None}
    placement2_info = {'placements': [(card_ks, 'middle', 0)], 'discarded': None}
    mock_child1 = MagicMock(spec=MCTSNode); mock_child1.visits = 10; mock_child1.total_reward = 50; mock_child1.placement_info = placement1_info
    mock_child2 = MagicMock(spec=MCTSNode); mock_child2.visits = 5; mock_child2.total_reward = 30; mock_child2.placement_info = placement2_info
    key1 = tuple(sorted(placement1_info['placements'])); key2 = tuple(sorted(placement2_info['placements']))
    mock_root.children = {key1: mock_child1, key2: mock_child2}

    MockMCTSNode.return_value = mock_root

    agent_instance = MCTSAgent(time_limit_ms=100)
    initial_board = PlayerBoard()
    cards_dealt = hand_to_int(['Ac', 'Kc'])
    remaining_deck = Deck.FULL_DECK_CARDS - set(c for c in cards_dealt if c is not None)

    placement_result = agent_instance.choose_placement(initial_board, cards_dealt, remaining_deck)

    MockMCTSNode.assert_called_once_with(board=initial_board, remaining_deck=remaining_deck, parent=None, placement_info=None)
    assert placement_result == placement1_info

def test_choose_placement_no_cards():
    agent = MCTSAgent(); board = PlayerBoard()
    assert agent.choose_placement(board, [], set()) is None

def test_choose_placement_complete_board():
    agent = MCTSAgent(); board = PlayerBoard()
    cards_optional = list(Deck.FULL_DECK_CARDS)[:13]; cards = [c for c in cards_optional if c is not None]
    if len(cards) < 13: pytest.skip("Not enough cards for full board test")
    board.set_full_board(cards[10:13], cards[5:10], cards[0:5])
    assert board.is_complete()
    assert agent.choose_placement(board, hand_to_int(['Ac']), set()) is None

# --- ИСПРАВЛЕНО: Тест MCTS цикла ---
@patch('mcts_agent.run_parallel_rollout') # Мокаем воркер
@patch('mcts_agent.MCTSNode') # Мокируем весь класс MCTSNode
def test_choose_placement_mcts_loop_simplified(MockMCTSNode, mock_rollout):
    """Тестирует, что цикл MCTS запускается и вызывает основные фазы с новой логикой."""
    # --- Настройка ---
    agent = MCTSAgent(time_limit_ms=100, num_workers=1, rollouts_per_leaf=1) # Увеличим время
    initial_board = PlayerBoard()
    cards_dealt = hand_to_int(['Ac', 'Kc', 'Qc'])
    deck = Deck.FULL_DECK_CARDS - set(c for c in cards_dealt if c is not None)

    # --- Мокирование Корня ---
    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = initial_board; mock_root.remaining_deck = deck
    mock_root.children = {}; mock_root.visits = 0; mock_root.total_reward = 0.0
    mock_root.is_terminal.return_value = False
    # Настраиваем _generate_next_states для корня
    mock_next_board1 = initial_board.copy(); mock_next_board1.add_card(Card.from_str('Ac'), 'top', 0); mock_next_board1.add_card(Card.from_str('Kc'), 'middle', 0)
    mock_discard1 = Card.from_str('Qc')
    mock_root._generate_next_states.return_value = [(mock_next_board1, mock_discard1)]
    mock_placement_info1 = {'placements': [(Card.from_str('Ac'), 'top', 0), (Card.from_str('Kc'), 'middle', 0)], 'discarded': mock_discard1}
    key1 = tuple(sorted(mock_placement_info1['placements']))
    mock_root._generated_states_for_expand = {key1: (mock_next_board1, mock_discard1, mock_placement_info1)}
    mock_root.untried_next_states = [(mock_next_board1, mock_discard1)]

    # --- Мокирование Дочернего Узла ---
    mock_child1 = MagicMock(spec=MCTSNode)
    mock_child1.board = mock_next_board1; mock_child1.remaining_deck = deck
    mock_child1.visits = 0; mock_child1.total_reward = 0.0
    mock_child1.is_terminal.return_value = False
    mock_child1.untried_next_states = None # Важно для триггера генерации
    mock_child1.placement_info = mock_placement_info1
    # Настраиваем _generate_next_states для дочернего узла
    mock_child1._generate_next_states.return_value = [] # Пусть вернет пустой список

    # --- Настройка Моков Методов Агента и Узлов ---
    # Настраиваем expand на корне: должен вернуть дочерний узел
    mock_root.expand.return_value = mock_child1
    # Настраиваем expand на дочернем узле: пусть возвращает None
    mock_child1.expand.return_value = None

    # Настраиваем _select: первый раз вернет корень, второй раз - дочерний узел
    select_calls = 0
    def select_side_effect(node):
        nonlocal select_calls
        select_calls += 1
        if select_calls == 1: return [mock_root], mock_root
        elif select_calls == 2: return [mock_root, mock_child1], mock_child1
        else: return [mock_root, mock_child1], mock_child1
    agent._select = MagicMock(side_effect=select_side_effect)

    # Настраиваем обратное распространение
    agent._backpropagate = MagicMock()
    mock_root.backpropagate = MagicMock()
    mock_child1.backpropagate = MagicMock()

    # --- Мокирование Роллаута ---
    mock_rollout.return_value = 5.0

    # --- Запуск MCTS ---
    MockMCTSNode.return_value = mock_root
    chosen_placement = agent.choose_placement(initial_board, cards_dealt, deck)

    # --- Проверки ---
    assert agent._select.call_count >= 2
    mock_root.expand.assert_called_once()
    # Проверяем вызов _generate_next_states на дочернем узле
    # Он вызывается внутри цикла MCTS перед попыткой expand для узла,
    # у которого untried_next_states is None
    mock_child1._generate_next_states.assert_called_once_with(ANY)
    assert mock_rollout.call_count > 0
    assert chosen_placement == mock_placement_info1


# --- Тест правила "Без трипса на топе" ---
# (Без изменений)
@patch('mcts_agent.run_parallel_rollout')
def test_choose_placement_no_trip_on_top_rule(mock_rollout):
    agent = MCTSAgent(time_limit_ms=200, num_workers=1, rollouts_per_leaf=2)
    initial_board = PlayerBoard()
    cards_dealt_list = ['2s', '2d', '2h', 'Ac', 'Kc']
    cards_dealt = hand_to_int(cards_dealt_list)
    remaining_deck = Deck.FULL_DECK_CARDS - set(c for c in cards_dealt if c is not None)

    card_2s = Card.from_str('2s'); card_2d = Card.from_str('2d'); card_2h = Card.from_str('2h')
    card_ac = Card.from_str('Ac'); card_kc = Card.from_str('Kc')

    bad_placements = [(card_2s, 'top', 0), (card_2d, 'top', 1), (card_2h, 'top', 2), (card_ac, 'middle', 0), (card_kc, 'middle', 1)]
    bad_placement_info = {'placements': bad_placements, 'discarded': None}
    bad_key = tuple(sorted(bad_placements))
    mock_bad_child = MagicMock(spec=MCTSNode); mock_bad_child.visits = 100; mock_bad_child.total_reward = 1000; mock_bad_child.placement_info = bad_placement_info

    good_placements = [(card_2s, 'bottom', 0), (card_2d, 'bottom', 1), (card_2h, 'bottom', 2), (card_ac, 'top', 0), (card_kc, 'top', 1)]
    good_placement_info = {'placements': good_placements, 'discarded': None}
    good_key = tuple(sorted(good_placements))
    mock_good_child = MagicMock(spec=MCTSNode); mock_good_child.visits = 10; mock_good_child.total_reward = 50; mock_good_child.placement_info = good_placement_info

    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = initial_board
    mock_root.children = {bad_key: mock_bad_child, good_key: mock_good_child}
    mock_root.visits = 110

    with patch('mcts_agent.MCTSNode', return_value=mock_root):
         with patch.object(agent, '_select', return_value=([mock_root], mock_root)):
             with patch.object(agent, '_backpropagate'):
                 chosen_placement = agent.choose_placement(initial_board, cards_dealt, remaining_deck)

    assert chosen_placement is not None
    assert chosen_placement == good_placement_info
