# tests/test_mcts_agent.py v2.0 (Refactored for Set Placement)
"""
Unit-тесты для модуля mcts_agent.py.
Обновлены для тестирования choose_placement и новой логики MCTSNode.
"""

import pytest
import time
from unittest.mock import patch, MagicMock, PropertyMock, call, ANY

# Импорты из тестируемых модулей и зависимостей
try:
    from mcts_agent import MCTSAgent
    # Импортируем MCTSNode и воркер для мока
    from mcts_node import MCTSNode, run_parallel_rollout
    from ofc_logic import PlayerBoard, Card, Deck, CARD_PLACEHOLDER, RANK_MAP
    from ofc_evaluators import evaluate_3_card_ofc, HAND_TYPE_TRIPS_3
except ImportError:
    pytest.skip("Skipping MCTS agent tests due to missing imports", allow_module_level=True)

# --- Хелперы ---
def hand_to_int(card_strs: list) -> list:
    return Card.hand_to_int(card_strs)

# --- Тесты Инициализации ---
# (Остаются без изменений, так как __init__ не менялся принципиально)
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
    assert agent.rollouts_per_leaf == 10 # Теперь не корректируется

# --- Тесты choose_placement ---

@patch('mcts_agent.MCTSNode') # Мокаем MCTSNode внутри агента
@patch('mcts_agent.multiprocessing.Pool') # Мокаем Pool
def test_choose_placement_basic_run(MockPool, MockMCTSNode):
    """Тестирует базовый запуск choose_placement без ошибок."""
    # --- Настройка Мока Корневого Узла ---
    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = PlayerBoard() # Пустая доска для корня
    mock_root.remaining_deck = Deck.FULL_DECK_CARDS.copy()
    mock_root.children = {}
    mock_root.visits = 0
    mock_root.total_reward = 0.0
    mock_root.untried_next_states = [] # Предполагаем, что состояния уже сгенерированы
    mock_root._generated_states_for_expand = {} # И словарь для expand тоже пуст

    # Мокируем _generate_next_states, чтобы он возвращал что-то
    # (хотя в choose_placement он вызывается для дочерних узлов, не для корня)
    # Но для _select_best_placement нужно, чтобы у корня были дети
    card_as = Card.from_str('As')
    card_ks = Card.from_str('Ks')
    placement1_info = {'placements': [(card_as, 'top', 0)], 'discarded': None}
    placement2_info = {'placements': [(card_ks, 'middle', 0)], 'discarded': None}

    # Создаем моки дочерних узлов
    mock_child1 = MagicMock(spec=MCTSNode)
    mock_child1.visits = 10; mock_child1.total_reward = 50; mock_child1.placement_info = placement1_info
    mock_child2 = MagicMock(spec=MCTSNode)
    mock_child2.visits = 5; mock_child2.total_reward = 30; mock_child2.placement_info = placement2_info

    # Ключи для словаря children - кортежи размещений
    key1 = tuple(sorted(placement1_info['placements']))
    key2 = tuple(sorted(placement2_info['placements']))
    mock_root.children = {key1: mock_child1, key2: mock_child2}

    # Устанавливаем, что MCTSNode() вернет наш мок корня
    MockMCTSNode.return_value = mock_root

    # --- Запуск Теста ---
    agent_instance = MCTSAgent(time_limit_ms=100) # Короткий лимит, т.к. MCTS не выполняется
    initial_board = PlayerBoard()
    cards_dealt = hand_to_int(['Ac', 'Kc']) # Карты, для которых ищем размещение
    remaining_deck = Deck.FULL_DECK_CARDS - set(c for c in cards_dealt if c is not None)

    # Запускаем choose_placement
    placement_result = agent_instance.choose_placement(initial_board, cards_dealt, remaining_deck)

    # --- Проверки ---
    # Проверяем, что MCTSNode был создан для корня
    MockMCTSNode.assert_called_once_with(
        board=initial_board,
        remaining_deck=remaining_deck,
        parent=None,
        placement_info=None
    )
    # Проверяем, что результат соответствует лучшему дочернему узлу (посещения > награда)
    assert placement_result == placement1_info # mock_child1 имеет больше посещений

def test_choose_placement_no_cards():
    """Тестирует вызов choose_placement без карт."""
    agent = MCTSAgent()
    board = PlayerBoard()
    assert agent.choose_placement(board, [], set()) is None

def test_choose_placement_complete_board():
    """Тестирует вызов choose_placement для полной доски."""
    agent = MCTSAgent()
    board = PlayerBoard()
    # Заполняем доску (содержимое не важно)
    cards_optional = list(Deck.FULL_DECK_CARDS)[:13]
    cards = [c for c in cards_optional if c is not None]
    if len(cards) < 13: pytest.skip("Not enough cards for full board test")
    board.set_full_board(cards[10:13], cards[5:10], cards[0:5])

    assert board.is_complete()
    assert agent.choose_placement(board, hand_to_int(['Ac']), set()) is None

# --- Тест MCTS цикла (упрощенный) ---
@patch('mcts_agent.run_parallel_rollout') # Мокаем воркер
@patch('mcts_agent.MCTSNode.expand') # Мокаем expand
@patch('mcts_agent.MCTSNode._generate_next_states') # Мокаем генерацию состояний
def test_choose_placement_mcts_loop_simplified(mock_generate_states, mock_expand, mock_rollout):
    """Тестирует, что цикл MCTS запускается и вызывает основные фазы с новой логикой."""
    # --- Настройка ---
    agent = MCTSAgent(time_limit_ms=50, num_workers=1, rollouts_per_leaf=1)
    initial_board = PlayerBoard()
    cards_dealt = hand_to_int(['Ac', 'Kc', 'Qc'])
    deck = Deck.FULL_DECK_CARDS - set(c for c in cards_dealt if c is not None)

    # --- Мокирование Корня и его инициализации ---
    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = initial_board
    mock_root.remaining_deck = deck
    mock_root.children = {}
    mock_root.visits = 0
    mock_root.total_reward = 0.0
    mock_root.is_terminal.return_value = False

    # Мокируем _generate_next_states для корня (вызывается в choose_placement)
    # Возвращаем одно возможное следующее состояние (доска, сброс)
    mock_next_board1 = initial_board.copy()
    mock_next_board1.add_card(Card.from_str('Ac'), 'top', 0)
    mock_next_board1.add_card(Card.from_str('Kc'), 'middle', 0)
    mock_discard1 = Card.from_str('Qc')
    mock_root.untried_next_states = [(mock_next_board1, mock_discard1)]
    # Сохраняем данные для expand
    mock_placement_info1 = {'placements': [(Card.from_str('Ac'), 'top', 0), (Card.from_str('Kc'), 'middle', 0)], 'discarded': mock_discard1}
    key1 = tuple(sorted(mock_placement_info1['placements']))
    mock_root._generated_states_for_expand = {key1: (mock_next_board1, mock_discard1, mock_placement_info1)}

    # --- Мокирование Выбора и Расширения ---
    # Мокируем uct_select_child, чтобы он возвращал корень (для первой итерации)
    # или мок дочернего узла
    mock_child1 = MagicMock(spec=MCTSNode)
    mock_child1.board = mock_next_board1
    mock_child1.remaining_deck = deck # Колода та же
    mock_child1.visits = 0
    mock_child1.total_reward = 0.0
    mock_child1.is_terminal.return_value = False
    mock_child1.untried_next_states = None # Еще не генерировал
    mock_child1.placement_info = mock_placement_info1

    # Настраиваем expand: при вызове на корне вернет mock_child1
    mock_expand.side_effect = lambda: mock_child1 if mock_expand.call_count == 1 else None

    # Настраиваем _select: первый раз вернет корень, второй раз - mock_child1
    def select_side_effect(node):
        if node == mock_root: return [mock_root], mock_root
        elif node == mock_child1: return [mock_root, mock_child1], mock_child1
        else: return [node], node # По умолчанию
    agent._select = MagicMock(side_effect=select_side_effect)

    # --- Мокирование Генерации Состояний для Дочернего Узла ---
    # Когда _select вернет mock_child1, для него будет вызвана генерация
    mock_generate_states.return_value = [] # Пусть для дочернего узла нет следующих состояний

    # --- Мокирование Роллаута ---
    mock_rollout.return_value = 5.0

    # --- Запуск MCTS ---
    with patch('mcts_agent.MCTSNode', return_value=mock_root): # Убедимся, что создается наш мок корня
        chosen_placement = agent.choose_placement(initial_board, cards_dealt, deck)

    # --- Проверки ---
    assert agent._select.call_count > 0 # Выбор вызывался
    assert mock_expand.call_count > 0 # Расширение вызывалось (хотя бы раз для корня)
    # Проверяем, что генерация состояний вызывалась для дочернего узла
    mock_generate_states.assert_called_once_with(ANY) # ANY т.к. симулированные карты случайны
    assert mock_rollout.call_count > 0 # Роллаут вызывался
    # Проверяем, что результат соответствует единственному сгенерированному ходу
    assert chosen_placement == mock_placement_info1

# --- Тест правила "Без трипса на топе" ---
@patch('mcts_agent.run_parallel_rollout') # Мокаем воркер
def test_choose_placement_no_trip_on_top_rule(mock_rollout):
    """Тестирует правило 'Без трипса на топе на улице 1' с новой логикой."""
    agent = MCTSAgent(time_limit_ms=200, num_workers=1, rollouts_per_leaf=2)

    # Ситуация: Улица 1 (пустая доска), 5 карт в руке, включая трипс двоек
    initial_board = PlayerBoard()
    cards_dealt_list = ['2s', '2d', '2h', 'Ac', 'Kc']
    cards_dealt = hand_to_int(cards_dealt_list)
    remaining_deck = Deck.FULL_DECK_CARDS - set(c for c in cards_dealt if c is not None)

    # Моделируем MCTS: создаем два возможных первых размещения
    card_2s = Card.from_str('2s')
    card_2d = Card.from_str('2d')
    card_2h = Card.from_str('2h')
    card_ac = Card.from_str('Ac')
    card_kc = Card.from_str('Kc')

    # "Плохое" размещение (трипс на топе)
    bad_placements = [
        (card_2s, 'top', 0), (card_2d, 'top', 1), (card_2h, 'top', 2),
        (card_ac, 'middle', 0), (card_kc, 'middle', 1)
    ]
    bad_placement_info = {'placements': bad_placements, 'discarded': None}
    bad_key = tuple(sorted(bad_placements))
    mock_bad_child = MagicMock(spec=MCTSNode); mock_bad_child.visits = 100; mock_bad_child.total_reward = 1000; mock_bad_child.placement_info = bad_placement_info

    # "Хорошее" размещение (трипс не на топе)
    good_placements = [
        (card_2s, 'bottom', 0), (card_2d, 'bottom', 1), (card_2h, 'bottom', 2),
        (card_ac, 'top', 0), (card_kc, 'top', 1)
    ]
    good_placement_info = {'placements': good_placements, 'discarded': None}
    good_key = tuple(sorted(good_placements))
    mock_good_child = MagicMock(spec=MCTSNode); mock_good_child.visits = 10; mock_good_child.total_reward = 50; mock_good_child.placement_info = good_placement_info # Меньше визитов/награда

    # Мокируем корневой узел и его детей
    mock_root = MagicMock(spec=MCTSNode)
    mock_root.board = initial_board
    mock_root.children = {bad_key: mock_bad_child, good_key: mock_good_child}
    mock_root.visits = 110 # Сумма визитов детей

    # Запускаем выбор лучшего размещения (MCTS цикл не нужен, только _select_best_placement)
    with patch('mcts_agent.MCTSNode', return_value=mock_root): # Мокируем создание корня
         # Мокируем основной цикл MCTS, чтобы он не выполнялся
         with patch.object(agent, '_select', return_value=([mock_root], mock_root)):
             with patch.object(agent, '_backpropagate'):
                 # Вызываем choose_placement, но ожидаем, что он быстро завершится
                 # и вызовет _select_best_placement для мока корня
                 chosen_placement = agent.choose_placement(initial_board, cards_dealt, remaining_deck)

    # Проверка: выбрано "хорошее" размещение, несмотря на лучшие MCTS-показатели "плохого"
    assert chosen_placement is not None
    assert chosen_placement == good_placement_info

# Убран тест _format_action, так как метод удален
