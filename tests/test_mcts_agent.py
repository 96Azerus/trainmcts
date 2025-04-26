# tests/test_mcts_agent.py v1.0
"""
Unit-тесты для модуля mcts_agent.py.
"""

import pytest
import time
from unittest.mock import patch, MagicMock, PropertyMock

# Импорты из тестируемых модулей и зависимостей
try:
    from mcts_agent import MCTSAgent
    from mcts_node import MCTSNode
    from ofc_logic import PlayerBoard, Card, Deck
    from ofc_evaluators import evaluate_3_card_ofc, HAND_TYPE_TRIPS_3
except ImportError:
    pytest.skip("Skipping MCTS agent tests due to missing imports", allow_module_level=True)

# --- Хелперы ---
def hand_to_int(card_strs: list) -> list:
    return Card.hand_to_int(card_strs)

# --- Тесты Инициализации ---
def test_mcts_agent_init_defaults():
    agent = MCTSAgent()
    assert agent.exploration == MCTSAgent.DEFAULT_EXPLORATION
    assert agent.time_limit == MCTSAgent.DEFAULT_TIME_LIMIT_MS / 1000.0
    assert agent.num_workers == MCTSAgent.DEFAULT_NUM_WORKERS
    assert agent.rollouts_per_leaf == MCTSAgent.DEFAULT_ROLLOUTS_PER_LEAF

def test_mcts_agent_init_custom():
    agent = MCTSAgent(exploration=2.0, time_limit_ms=1000, num_workers=1, rollouts_per_leaf=10)
    assert agent.exploration == 2.0
    assert agent.time_limit == 1.0
    assert agent.num_workers == 1
    # rollouts_per_leaf должен стать 1, если worker=1
    assert agent.rollouts_per_leaf == 1

# --- Тесты choose_action ---

@patch('mcts_agent.MCTSNode') # Мокаем MCTSNode внутри агента
@patch('mcts_agent.multiprocessing.Pool') # Мокаем Pool
def test_choose_action_basic_run(MockPool, MockMCTSNode):
    """Тестирует базовый запуск MCTS без ошибок."""
    # Настройка моков
    mock_root = MagicMock(spec=MCTSNode)
    mock_root.children = {} # Изначально нет детей
    mock_root.visits = 0
    mock_root.total_reward = 0.0
    mock_root._get_available_placements.return_value = [ # Возвращаем пару действий
        (Card.from_str('Ac'), 'top', 0),
        (Card.from_str('Ac'), 'middle', 0)
    ]
    MockMCTSNode.return_value = mock_root # Конструктор возвращает наш мок

    # Мок для выбора лучшего действия
    agent_instance = MCTSAgent(time_limit_ms=100) # Короткий лимит времени
    agent_instance._select_best_action = MagicMock(return_value=(Card.from_str('Ac'), 'top', 0))

    # Входные данные
    board = PlayerBoard()
    cards_to_place = hand_to_int(['Ac', 'Kc'])
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_to_place)

    action = agent_instance.choose_action(board, cards_to_place, remaining_deck)

    # Проверки
    assert action == (Card.from_str('Ac'), 'top', 0)
    MockMCTSNode.assert_called_once() # Проверяем, что корневой узел был создан
    # Проверяем, что _select_best_action был вызван с корневым узлом
    agent_instance._select_best_action.assert_called_once_with(mock_root, board, cards_to_place)
    # Проверяем, что Pool не создавался (т.к. воркеры по умолчанию > 1, но мы не дошли до роллаутов)
    # MockPool.assert_not_called() # Это может быть не так, если воркер > 1

def test_choose_action_no_cards():
    agent = MCTSAgent()
    board = PlayerBoard()
    assert agent.choose_action(board, [], set()) is None

def test_choose_action_complete_board():
    agent = MCTSAgent()
    board = PlayerBoard()
    # Заполняем доску (неважно чем)
    cards = list(Deck.FULL_DECK_CARDS)[:13]
    board.set_full_board(cards[10:13], cards[5:10], cards[0:5])
    assert board.is_complete()
    assert agent.choose_action(board, hand_to_int(['Ac']), set()) is None

@patch('mcts_agent.MCTSNode')
@patch('mcts_agent.MCTSNode.expand')
@patch('mcts_agent.MCTSNode.backpropagate')
@patch('mcts_agent.run_parallel_rollout') # Мокаем воркер
def test_choose_action_mcts_loop(mock_rollout, mock_backprop, mock_expand, MockMCTSNode):
    """Тестирует выполнение нескольких итераций MCTS."""
    # Настройка
    mock_root = MagicMock(spec=MCTSNode)
    mock_child1 = MagicMock(spec=MCTSNode)
    mock_child2 = MagicMock(spec=MCTSNode)

    # Настройка поведения моков
    MockMCTSNode.return_value = mock_root
    mock_root.is_terminal.return_value = False
    mock_root.untried_actions = [] # Сначала нет неиспробованных
    mock_root.children = {'action1': mock_child1, 'action2': mock_child2}
    mock_root.visits = 10 # Устанавливаем посещения для UCT

    # Моделируем выбор детей
    # Первый выбор - child1 (для расширения)
    # Второй выбор - child2 (для роллаута)
    mock_root.uct_select_child.side_effect = [mock_child1, mock_child2]

    # Моделируем расширение child1
    mock_child1.is_terminal.return_value = False
    mock_child1.untried_actions = ['action1_1'] # Есть неиспробованное действие
    mock_expand.return_value = MagicMock(spec=MCTSNode) # Возвращаем мок расширенного узла
    mock_expand.return_value.is_terminal.return_value = False # Расширенный узел не терминальный

    # Моделируем роллаут из child2
    mock_child2.is_terminal.return_value = False
    mock_child2.untried_actions = [] # Нет неиспробованных
    mock_child2.children = {} # Нет детей (лист)
    mock_rollout.return_value = 5.0 # Роллаут возвращает 5 роялти

    agent = MCTSAgent(time_limit_ms=100, num_workers=1, rollouts_per_leaf=1) # 1 воркер, 1 роллаут
    agent._select_best_action = MagicMock(return_value='action1') # Мок выбора лучшего

    board = PlayerBoard()
    cards = hand_to_int(['Ac', 'Kc', 'Qc'])
    deck = Deck.FULL_DECK_CARDS - set(cards)

    # Запускаем MCTS
    agent.choose_action(board, cards, deck)

    # Проверки
    assert mock_expand.call_count >= 1 # Должны были попытаться расширить
    assert mock_rollout.call_count >= 1 # Должны были сделать роллаут
    assert mock_backprop.call_count >= 1 # Должны были сделать backpropagate

@patch('mcts_agent.MCTSNode')
@patch('mcts_agent.MCTSAgent._select_best_action') # Мокаем финальный выбор
def test_choose_action_no_trip_on_top_rule(mock_select_best, MockMCTSNode):
    """Тестирует правило 'Без трипса на топе на улице 1'."""
    agent = MCTSAgent(time_limit_ms=100)

    # Ситуация: Улица 1 (пустая доска), 5 карт в руке, включая трипс двоек
    board = PlayerBoard()
    cards_to_place = hand_to_int(['2s', '2d', '2h', 'Ac', 'Kc'])
    remaining_deck = Deck.FULL_DECK_CARDS - set(cards_to_place)

    # Моделируем MCTS, который *хочет* поставить двойку на топ
    mock_root = MagicMock(spec=MCTSNode)
    MockMCTSNode.return_value = mock_root

    # Действие, которое нарушает правило
    bad_action = (Card.from_str('2s'), 'top', 0)
    # Действие, которое не нарушает правило
    good_action = (Card.from_str('2s'), 'middle', 0)
    # Другое хорошее действие
    good_action2 = (Card.from_str('Ac'), 'bottom', 0)

    # Моделируем результат MCTS: bad_action имеет больше всего посещений
    mock_child_bad = MagicMock(spec=MCTSNode); mock_child_bad.visits = 100; mock_child_bad.total_reward = 50
    mock_child_good = MagicMock(spec=MCTSNode); mock_child_good.visits = 80; mock_child_good.total_reward = 45
    mock_child_good2 = MagicMock(spec=MCTSNode); mock_child_good2.visits = 70; mock_child_good2.total_reward = 40
    mock_root.children = {
        bad_action: mock_child_bad,
        good_action: mock_child_good,
        good_action2: mock_child_good2
    }

    # Запускаем choose_action
    chosen_action = agent.choose_action(board, cards_to_place, remaining_deck)

    # Проверка: Должно быть выбрано ЛУЧШЕЕ из РАЗРЕШЕННЫХ действий (good_action)
    assert chosen_action is not None
    assert chosen_action == good_action
    # Убедимся, что _select_best_action был вызван (он содержит логику правила)
    mock_select_best.assert_called_once()

def test_format_action():
    """Тестирует форматирование действия."""
    agent = MCTSAgent()
    action1 = (Card.from_str('As'), 'top', 0)
    assert agent._format_action(action1) == "As@top[0]"
    action2 = None
    assert agent._format_action(action2) == "None"
    action3 = "some string" # Некорректный формат
    assert agent._format_action(action3) == "some string"
