# tests/test_mcts_agent.py v1.3
"""
Unit-тесты для mcts_agent.py.
ИСПРАВЛЕНО: Ожидание в test_choose_placement_basic_run.
ИСПРАВЛЕНО: Сигнатура select_side_effect в test_choose_placement_mcts_loop_simplified.
"""
import pytest
from unittest.mock import MagicMock, patch, ANY
import time

try:
    from mcts_agent import MCTSAgent
    from mcts_node import MCTSNode, run_parallel_rollout # Добавили run_parallel_rollout для мока
    from ofc_logic import PlayerBoard, Card, Deck, hand_to_int as logic_hand_to_int, RANK_MAP
except ImportError:
    pytest.skip("Skipping MCTS agent tests due to missing core imports", allow_module_level=True)


def hand_to_int(card_strs: list) -> list:
    """Конвертирует список строк в список int карт, пропуская None."""
    return [c for c in logic_hand_to_int(card_strs) if c is not None]


@pytest.fixture
def agent_default():
    return MCTSAgent()

def test_mcts_agent_init_defaults(agent_default):
    assert agent_default.exploration == MCTSAgent.DEFAULT_EXPLORATION
    assert agent_default.time_limit == MCTSAgent.DEFAULT_TIME_LIMIT_MS / 1000.0
    assert agent_default.num_workers == MCTSAgent.DEFAULT_NUM_WORKERS
    assert agent_default.rollouts_per_leaf == MCTSAgent.DEFAULT_ROLLOUTS_PER_LEAF

def test_mcts_agent_init_custom():
    agent = MCTSAgent(exploration=2.0, time_limit_ms=1000, num_workers=4, rollouts_per_leaf=5)
    assert agent.exploration == 2.0
    assert agent.time_limit == 1.0
    assert agent.num_workers == 4
    assert agent.rollouts_per_leaf == 5

@patch('mcts_agent.MCTSNode')
@patch('mcts_agent.multiprocessing.Pool') # Мокаем Pool
def test_choose_placement_basic_run(MockPool, MockMCTSNode):
    """Тестирует базовый запуск choose_placement без ошибок."""
    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = PlayerBoard()
    mock_root.remaining_deck = Deck.FULL_DECK_CARDS.copy()
    mock_root.children = {}
    mock_root.visits = 0; mock_root.total_reward = 0.0
    mock_root.rave_visits = 0; mock_root.rave_reward = 0.0
    mock_root.untried_next_states = []
    mock_root._generated_states_for_expand = {}

    card_as = Card.from_str('As'); card_ks = Card.from_str('Ks')
    # Изначально было: placement1_info (As top), placement2_info (Ks middle)
    # Сортировка: (avg_reward, visits)
    # child1: visits=10, total_reward=50.0 (avg_reward=5.0) -> placement1_info (As top)
    # child2: visits=5, total_reward=30.0 (avg_reward=6.0)  -> placement2_info (Ks middle)
    # Ожидаем, что child2 (placement2_info) будет выбран, так как avg_reward выше.
    placement1_info = {'placements': [(card_as, 'top', 0)], 'discarded': None} # avg_reward = 5.0
    placement2_info = {'placements': [(card_ks, 'middle', 0)], 'discarded': None} # avg_reward = 6.0

    mock_child1 = MagicMock(spec=MCTSNode)
    mock_child1.visits = 10; mock_child1.total_reward = 50.0
    mock_child1.rave_visits = 15; mock_child1.rave_reward = 70.0
    mock_child1.placement_info = placement1_info

    mock_child2 = MagicMock(spec=MCTSNode)
    mock_child2.visits = 5; mock_child2.total_reward = 30.0
    mock_child2.rave_visits = 8; mock_child2.rave_reward = 40.0
    mock_child2.placement_info = placement2_info
    
    key1 = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placement1_info['placements']]))
    key2 = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placement2_info['placements']]))
    mock_root.children = {key1: mock_child1, key2: mock_child2}

    MockMCTSNode.return_value = mock_root
    
    # Мокаем run_parallel_rollout, чтобы он возвращал корректные значения и не падал
    # из-за проблем в mcts_node, если они еще есть
    with patch('mcts_agent.run_parallel_rollout', return_value=(1.0, [])) as mock_run_rollout:
        agent_instance = MCTSAgent(time_limit_ms=10, num_workers=1, rollouts_per_leaf=1) # num_workers=1 чтобы не использовать Pool
        # Мокаем _select, чтобы он возвращал корень для немедленного выбора лучшего ребенка
        with patch.object(agent_instance, '_select', return_value=([mock_root], mock_root)):
            with patch.object(agent_instance, '_backpropagate_standard'):
                with patch.object(agent_instance, '_backpropagate_rave'):
                    initial_board = PlayerBoard()
                    cards_dealt = hand_to_int(['Ac', 'Kc']) # 2 карты
                    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)
                    
                    # Устанавливаем, что MCTS цикл не будет выполняться долго
                    # (например, time.time() будет сразу больше start_time + time_limit)
                    # Это не нужно, если _select мокнут так, что он сразу возвращает корень
                    # и rollouts_per_leaf = 1 и time_limit_ms очень мал.

                    placement_result = agent_instance.choose_placement(initial_board, cards_dealt, remaining_deck)

    MockMCTSNode.assert_called_once_with(board=initial_board, remaining_deck=remaining_deck, parent=None, placement_info=None)
    # ИСПРАВЛЕНО: Ожидаем placement2_info, так как у него выше avg_reward (6.0 vs 5.0)
    assert placement_result == placement2_info, \
        f"Expected {placement2_info} (avg_reward 6.0) but got {placement_result}"


def test_choose_placement_no_cards(agent_default):
    board = PlayerBoard()
    assert agent_default.choose_placement(board, [], Deck.FULL_DECK_CARDS) is None

def test_choose_placement_complete_board(agent_default):
    board = PlayerBoard()
    for i in range(13): # Заполняем доску
        # Просто для примера, не важно какими картами
        card_int = Card.from_str(f"{STR_RANKS[i%13]}s")
        if i < 3: board.add_card(card_int, 'top', i)
        elif i < 8: board.add_card(card_int, 'middle', i-3)
        else: board.add_card(card_int, 'bottom', i-8)
    
    assert board.is_complete()
    assert agent_default.choose_placement(board, [Card.from_str('As')], Deck.FULL_DECK_CARDS) is None


@patch('mcts_agent.run_parallel_rollout')
@patch('mcts_agent.MCTSNode')
def test_choose_placement_mcts_loop_simplified(MockMCTSNode, mock_rollout_func):
    """Тестирует, что цикл MCTS запускается и вызывает основные фазы с новой логикой."""
    agent = MCTSAgent(time_limit_ms=50, num_workers=1, rollouts_per_leaf=1) # num_workers=1
    initial_board = PlayerBoard()
    cards_dealt = hand_to_int(['Ac', 'Kc', 'Qc'])
    deck = Deck.FULL_DECK_CARDS - set(cards_dealt)

    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = initial_board; mock_root.remaining_deck = deck
    mock_root.children = {}; mock_root.visits = 0; mock_root.total_reward = 0.0
    mock_root.rave_visits = 0; mock_root.rave_reward = 0.0
    mock_root.is_terminal.return_value = False
    
    # Информация для первого расширения
    mock_next_board1 = initial_board.copy()
    # Размещаем 2 из 3 карт, одну сбрасываем
    mock_next_board1.add_card(Card.from_str('Ac'), 'top', 0)
    mock_next_board1.add_card(Card.from_str('Kc'), 'middle', 0)
    mock_discard1 = Card.from_str('Qc')
    
    mock_placement_info1 = {'placements': [(Card.from_str('Ac'), 'top', 0), (Card.from_str('Kc'), 'middle', 0)], 'discarded': mock_discard1}
    key1_placements = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in mock_placement_info1['placements']]))
    key1 = (key1_placements, mock_discard1)

    # _generate_next_states должна быть вызвана на корневом узле
    # и вернуть список (board, discarded_card)
    # _generated_states_for_expand будет заполнен внутри _generate_next_states
    mock_root._generate_next_states.return_value = [(mock_next_board1, mock_discard1)]
    mock_root._generated_states_for_expand = {key1: (mock_next_board1, mock_discard1, mock_placement_info1)}
    mock_root.untried_next_states = None # Изначально None, чтобы _select вызвал генерацию

    mock_child1 = MagicMock(spec=MCTSNode)
    mock_child1.board = mock_next_board1; mock_child1.remaining_deck = deck - {Card.from_str('Ac'), Card.from_str('Kc'), mock_discard1}
    mock_child1.visits = 0; mock_child1.total_reward = 0.0
    mock_child1.rave_visits = 0; mock_child1.rave_reward = 0.0
    mock_child1.is_terminal.return_value = False
    mock_child1.untried_next_states = None # Для простоты, этот ребенок не будет дальше генерировать
    mock_child1.placement_info = mock_placement_info1
    mock_child1._generate_next_states.return_value = [] # Не будет генерировать дальше
    
    mock_root.expand.return_value = mock_child1 # expand() на корне вернет mock_child1
    mock_child1.expand.return_value = None # expand() на ребенке ничего не вернет

    select_calls = 0
    # ИСПРАВЛЕНО: сигнатура select_side_effect
    def select_side_effect(node, cards_for_node): # Принимает 2 аргумента
        nonlocal select_calls
        select_calls += 1
        if select_calls == 1: # Первый вызов _select
            # MCTSNode._generate_next_states будет вызван внутри _select для root_node
            # Затем _select вернет root_node для expand
            node.untried_next_states = node._generate_next_states(cards_for_node) # Имитируем вызов генерации
            return [node], node # Возвращаем корень для expand
        elif select_calls == 2: # Второй вызов _select
            # После expand, mock_root будет иметь mock_child1.
            # _select должен выбрать mock_child1 (т.к. он неисследован)
            return [node, mock_child1], mock_child1 # Возвращаем путь до mock_child1
        else: # Последующие вызовы
            return [node, mock_child1], mock_child1 # Продолжаем выбирать mock_child1
    
    agent._select = MagicMock(side_effect=select_side_effect)
    agent._backpropagate_standard = MagicMock()
    agent._backpropagate_rave = MagicMock()
    
    mock_rollout_func.return_value = (5.0, []) # run_parallel_rollout возвращает (reward, actions)
    MockMCTSNode.return_value = mock_root # Когда создается MCTSAgent, он создает root_node

    chosen_placement = agent.choose_placement(initial_board, cards_dealt, deck)

    assert agent._select.call_count >= 1
    mock_root.expand.assert_called_once() # Корень должен быть расширен
    mock_rollout_func.assert_called() # Роллаут должен быть вызван
    agent._backpropagate_standard.assert_called()
    agent._backpropagate_rave.assert_called()
    
    # Проверяем, что выбран лучший (и единственный в данном случае) ребенок
    assert chosen_placement == mock_placement_info1


def test_choose_placement_no_trip_on_top_rule(agent_default):
    """Тест: ИИ не должен ставить трипс на топ на первой улице."""
    board = PlayerBoard()
    # Рука: As Ad Ac Ks Qh (Три туза)
    cards_dealt = hand_to_int(['As', 'Ad', 'Ac', 'Ks', 'Qh'])
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_dealt)

    # Уменьшаем время, чтобы тест не был слишком долгим, но MCTS успел поработать
    agent_default.time_limit = 0.2 # 200 ms
    agent_default.rollouts_per_leaf = 5

    placement_info = agent_default.choose_placement(board, cards_dealt, remaining_deck)
    assert placement_info is not None
    
    top_cards_placed_ranks = []
    for card_int, row, _ in placement_info['placements']:
        if row == 'top':
            top_cards_placed_ranks.append(Card.get_rank_int(card_int))
    
    rank_counts_top = Counter(top_cards_placed_ranks)
    has_trip_on_top = any(count >= 3 for count in rank_counts_top.values())
    
    assert not has_trip_on_top, "AI placed a trip on the top row on the first street"
