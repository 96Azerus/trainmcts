# tests/test_mcts_agent.py v1.4 (Adapted for new MCTSNode structure and "?" cards)
"""
Unit-тесты для mcts_agent.py.
- Адаптированы для новой структуры MCTSNode и передачи num_unknown_removed_cards.
- Улучшена проверка вызовов и аргументов.
"""
import pytest
from unittest.mock import MagicMock, patch, ANY
import time
from collections import Counter, defaultdict # Добавлен defaultdict

try:
    from mcts_agent import MCTSAgent
    from mcts_node import MCTSNode, run_parallel_rollout, HEURISTIC_FOUL_PENALTY
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS
except ImportError:
    pytest.skip("Skipping MCTS agent tests due to missing core imports", allow_module_level=True)

def hand_to_int(card_strs: list) -> list:
    return [c for c in Card.hand_to_int(card_strs) if c is not None]

@pytest.fixture
def agent_default():
    return MCTSAgent(time_limit_ms=50) # Короткое время для быстрых тестов

def test_mcts_agent_init_defaults():
    agent = MCTSAgent() # Используем значения по умолчанию из класса
    assert agent.exploration == MCTSAgent.DEFAULT_EXPLORATION
    assert agent.time_limit == MCTSAgent.DEFAULT_TIME_LIMIT_MS / 1000.0
    assert agent.num_workers == MCTSAgent.DEFAULT_NUM_WORKERS
    assert agent.rollouts_per_leaf == MCTSAgent.DEFAULT_ROLLOUTS_PER_LEAF

def test_mcts_agent_init_custom():
    agent = MCTSAgent(exploration=2.0, time_limit_ms=1000, num_workers=4, rollouts_per_leaf=5)
    assert agent.exploration == 2.0; assert agent.time_limit == 1.0
    assert agent.num_workers == 4; assert agent.rollouts_per_leaf == 5

@patch('mcts_agent.MCTSNode')
@patch('mcts_agent.multiprocessing.Pool')
def test_choose_placement_basic_run_and_selection(MockPool, MockMCTSNode, agent_default):
    initial_board = PlayerBoard()
    initial_board.add_card(Card.from_str('2c'), 'bottom', 0)

    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = initial_board
    mock_root.remaining_deck = Deck.FULL_DECK_CARDS.copy()
    mock_root.children = {}; mock_root.visits = 0; mock_root.total_reward = 0.0
    mock_root.rave_visits_count = 0; mock_root.rave_total_reward = 0.0
    mock_root.untried_next_states = []; mock_root._generated_states_for_expand = {}
    mock_root.num_unknown_removed_cards = 0

    card_as = Card.from_str('As'); card_ks = Card.from_str('Ks')
    p_info1 = {'placements': [(card_as, 'top', 0)], 'discarded': card_ks, 'score': 50}
    p_info2 = {'placements': [(card_ks, 'middle', 0)], 'discarded': card_as, 'score': 60}

    mock_child1 = MagicMock(spec=MCTSNode); mock_child1.visits = 10; mock_child1.total_reward = 50.0
    mock_child1.rave_visits_count = 15; mock_child1.rave_total_reward = 70.0
    mock_child1.placement_info = p_info1; mock_child1.num_unknown_removed_cards = 0

    mock_child2 = MagicMock(spec=MCTSNode); mock_child2.visits = 5; mock_child2.total_reward = 30.0 # 30/5 = 6.0
    mock_child2.rave_visits_count = 8; mock_child2.rave_total_reward = 40.0
    mock_child2.placement_info = p_info2; mock_child2.num_unknown_removed_cards = 0

    key1_pl = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in p_info1['placements']]))
    key1 = (key1_pl, p_info1['discarded'])
    key2_pl = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in p_info2['placements']]))
    key2 = (key2_pl, p_info2['discarded'])
    mock_root.children = {key1: mock_child1, key2: mock_child2}

    MockMCTSNode.return_value = mock_root

    with patch('mcts_agent.run_parallel_rollout', return_value=(1.0, [])):
        agent_instance = MCTSAgent(time_limit_ms=10, num_workers=1, rollouts_per_leaf=1)
        with patch.object(agent_instance, '_select', return_value=([mock_root], mock_root)):
            with patch.object(agent_instance, '_backpropagate_standard'):
                with patch.object(agent_instance, '_backpropagate_rave'):
                    cards_dealt = hand_to_int(['Ac', 'Kc'])
                    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt) - {Card.from_str('2c')}
                    num_unknown = 1

                    placement_result = agent_instance.choose_placement(initial_board, cards_dealt, remaining_deck, num_unknown)

    MockMCTSNode.assert_called_once_with(
        board=ANY, remaining_deck=ANY, parent=None, placement_info=None, num_unknown_removed_cards=num_unknown
    )
    assert isinstance(MockMCTSNode.call_args.kwargs['board'], PlayerBoard)
    assert isinstance(MockMCTSNode.call_args.kwargs['remaining_deck'], set)
    assert placement_result == p_info2, f"Expected {p_info2} (avg_reward 6.0) but got {placement_result}"


def test_choose_placement_no_cards(agent_default):
    board = PlayerBoard()
    assert agent_default.choose_placement(board, [], Deck.FULL_DECK_CARDS.copy(), 0) is None

def test_choose_placement_no_available_slots(agent_default):
    board = PlayerBoard()
    all_cards_list = list(Deck.FULL_DECK_CARDS)
    full_board_cards = set()
    for i in range(PlayerBoard.TOTAL_CAPACITY):
        card_to_add = all_cards_list[i]
        full_board_cards.add(card_to_add)
        if i < 3: board.add_card(card_to_add, 'top', i)
        elif i < 8: board.add_card(card_to_add, 'middle', i - 3)
        else: board.add_card(card_to_add, 'bottom', i - 8)

    assert board.is_complete()
    cards_dealt = [all_cards_list[13], all_cards_list[14]]
    remaining_deck = Deck.FULL_DECK_CARDS.copy() - full_board_cards - set(cards_dealt)
    result = agent_default.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert result is not None
    assert result['placements'] == []
    assert result['discarded'] == tuple(sorted(cards_dealt))
    assert result['reason'] == "No slots available, discarding all dealt cards."


@patch('mcts_agent.run_parallel_rollout')
@patch('mcts_agent.MCTSNode')
def test_choose_placement_mcts_loop_flow(MockMCTSNode, mock_rollout_func, agent_default):
    initial_board = PlayerBoard()
    cards_dealt = hand_to_int(['Ac', 'Kc', 'Qc'])
    deck = Deck.FULL_DECK_CARDS - set(cards_dealt)
    num_unknown = 2

    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = initial_board; mock_root.remaining_deck = deck; mock_root.num_unknown_removed_cards = num_unknown
    mock_root.children = {}; mock_root.visits = 0; mock_root.total_reward = 0.0
    mock_root.rave_visits_count = 0; mock_root.rave_total_reward = 0.0
    mock_root.is_terminal.return_value = False
    mock_root._generated_states_for_expand = {}

    mock_next_board1 = initial_board.copy(); mock_next_board1.add_card(Card.from_str('Ac'), 'top', 0); mock_next_board1.add_card(Card.from_str('Kc'), 'middle', 0)
    mock_discard1 = Card.from_str('Qc')
    p_info1 = {'placements': [(Card.from_str('Ac'),'top',0), (Card.from_str('Kc'),'middle',0)], 'discarded': mock_discard1, 'score':10}
    key1_pl = tuple(sorted([(p[0],p[1],p[2]) for p in p_info1['placements']]))
    key1 = (key1_pl, mock_discard1)

    mock_root._generate_next_states.return_value = [(mock_next_board1, mock_discard1)]
    mock_root._generated_states_for_expand = {key1: (mock_next_board1, mock_discard1, p_info1)}
    mock_root.untried_next_states = None

    mock_child1 = MagicMock(spec=MCTSNode)
    mock_child1.board = mock_next_board1; mock_child1.remaining_deck = deck - {Card.from_str('Ac'), Card.from_str('Kc'), mock_discard1}
    mock_child1.visits = 0; mock_child1.total_reward = 0.0; mock_child1.rave_visits_count = 0; mock_child1.rave_total_reward = 0.0
    mock_child1.is_terminal.return_value = False; mock_child1.untried_next_states = None
    mock_child1.placement_info = p_info1; mock_child1.num_unknown_removed_cards = num_unknown
    mock_child1._generate_next_states.return_value = []
    # ИСПРАВЛЕНИЕ: Добавляем недостающий атрибут 'children' к моку.
    # Причина: Тест падал с AttributeError, так как MCTS-цикл пытался получить доступ к этому атрибуту у мока.
    mock_child1.children = {}

    mock_root.expand.return_value = mock_child1
    mock_child1.expand.return_value = None

    select_call_count = 0
    def select_effect(node, cards_for_node):
        nonlocal select_call_count; select_call_count += 1
        if node is mock_root and select_call_count == 1:
            node.untried_next_states = node._generate_next_states(cards_for_node)
            return [node], node
        return [node, mock_child1], mock_child1

    agent_instance = MCTSAgent(time_limit_ms=10, num_workers=1, rollouts_per_leaf=1)
    agent_instance._select = MagicMock(side_effect=select_effect)
    agent_instance._backpropagate_standard = MagicMock()
    agent_instance._backpropagate_rave = MagicMock()
    mock_rollout_func.return_value = (5.0, [p_info1])
    MockMCTSNode.return_value = mock_root

    chosen_placement = agent_instance.choose_placement(initial_board, cards_dealt, deck, num_unknown)

    assert agent_instance._select.call_count >= 1
    mock_root.expand.assert_called_once()
    mock_rollout_func.assert_called()
    agent_instance._backpropagate_standard.assert_called()
    agent_instance._backpropagate_rave.assert_called()
    assert chosen_placement == p_info1


def test_choose_placement_no_trip_on_top_rule(agent_default):
    board = PlayerBoard()
    cards_dealt = hand_to_int(['As', 'Ad', 'Ac', 'Ks', 'Qh']) # Три туза
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)
    agent_default.time_limit = 0.2; agent_default.rollouts_per_leaf = 5

    placement_info = agent_default.choose_placement(board, cards_dealt, remaining_deck, 0)
    assert placement_info is not None

    top_cards_ranks = [Card.get_rank_int(p[0]) for p in placement_info['placements'] if p[1] == 'top']
    rank_counts_top = Counter(top_cards_ranks)
    assert not any(count >= 3 for count in rank_counts_top.values()), "AI placed trip on top (1st street)"

def test_backpropagate_rave_updates_child_stats(agent_default):
    """Тест: _backpropagate_rave должен обновлять RAVE-статистику детей."""
    root = MCTSNode(PlayerBoard(), set(), num_unknown_removed_cards=0)

    # Действие 1 (ребенок 1)
    p_info1 = {'placements': [(Card.from_str('As'), 'top', 0)], 'discarded': Card.from_str('Ks')}
    key1_pl = tuple(sorted([(p[0],p[1],p[2]) for p in p_info1['placements']]))
    key1 = (key1_pl, p_info1['discarded'])
    child1 = MCTSNode(PlayerBoard(), set(), parent=root, placement_info=p_info1, num_unknown_removed_cards=0)
    root.children[key1] = child1

    # Действие 2 (ребенок 2)
    p_info2 = {'placements': [(Card.from_str('Ad'), 'top', 1)], 'discarded': Card.from_str('Qs')}
    key2_pl = tuple(sorted([(p[0],p[1],p[2]) for p in p_info2['placements']]))
    key2 = (key2_pl, p_info2['discarded'])
    child2 = MCTSNode(PlayerBoard(), set(), parent=root, placement_info=p_info2, num_unknown_removed_cards=0)
    root.children[key2] = child2

    path_to_leaf = [root] # Роллаут начался с корня (лист не важен для этого теста)

    # Симуляция, в которой было совершено действие, ведущее к child1
    simulation_actions_history = [p_info1, {'placements': [(Card.from_str('2s'),'mid',0)], 'discarded':None}] # p_info1 + еще одно действие

    agent_default._backpropagate_rave(path_to_leaf, simulation_actions_history, reward=10.0)

    assert child1.rave_visits_count == 1
    assert child1.rave_total_reward == 10.0
    assert child2.rave_visits_count == 0 # child2 не был в истории симуляции
    assert child2.rave_total_reward == 0.0

    # Еще одна симуляция, снова с child1
    agent_default._backpropagate_rave(path_to_leaf, simulation_actions_history, reward=5.0)
    assert child1.rave_visits_count == 2
    assert child1.rave_total_reward == 15.0
