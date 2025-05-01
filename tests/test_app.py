# tests/test_app.py v2.0 (Refactored for Set Placement MCTS)
"""
Интеграционные тесты для Flask приложения app.py.
Обновлены для работы с choose_placement и новым API.
Добавлены тесты для /calculate_score, /update_state, /reset_game_state.
"""

import pytest
import json
from unittest.mock import patch, MagicMock

# Импортируем Flask app и зависимости
try:
    from app import app as flask_app
    from ofc_logic import Card, PlayerBoard, CARD_PLACEHOLDER, Deck # Добавлен Deck
    # Импортируем MCTSАgent для мока
    from mcts_agent import MCTSAgent
    # Импортируем функции оценки для мока в тестах /calculate_score
    from ofc_evaluators import check_board_foul, get_row_royalty
except ImportError:
    pytest.skip("Skipping app tests due to missing imports", allow_module_level=True)

# --- Фикстура для Flask test client ---
@pytest.fixture
def client():
    """Создает тестовый клиент Flask."""
    flask_app.config['TESTING'] = True
    # flask_app.logger.setLevel(logging.CRITICAL) # Раскомментировать для подавления логов
    with flask_app.test_client() as client:
        yield client

# --- Тесты эндпоинта / ---
def test_index_route(client):
    """Тестирует GET запрос к корневому маршруту."""
    response = client.get('/')
    assert response.status_code == 200
    assert b"<title>OFC Training Mode</title>" in response.data

# --- Тесты эндпоинта /ai_move ---

# Мокаем MCTSAgent целиком для тестов /ai_move
@patch('app.MCTSAgent')
def test_ai_move_valid_request(MockMCTSAgent, client):
    """Тестирует валидный POST запрос к /ai_move с новой логикой."""
    # Настройка мока агента и его метода choose_placement
    mock_agent_instance = MockMCTSAgent.return_value
    # Моделируем возвращаемое значение choose_placement
    card_as_int = Card.from_str('As')
    card_ks_int = Card.from_str('Ks')
    card_qs_int = Card.from_str('Qs') # Для сброса
    mock_placement_info = {
        'placements': [
            (card_as_int, 'top', 0),
            (card_ks_int, 'middle', 0)
        ],
        'discarded': card_qs_int
    }
    mock_agent_instance.choose_placement.return_value = mock_placement_info

    # Данные запроса (улица 2+, 3 карты)
    request_data = {
        "selected_cards": ["As", "Ks", "Qs"], # Карты для размещения/сброса
        "board": { # Пустая доска для простоты
            "top": [None, None, None],
            "middle": [None] * 5,
            "bottom": [None] * 5
        },
        "discarded_cards": ["2c", "3d"], # Перманентно удаленные
        "ai_settings": {"aiTime": 1}
    }

    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 200
    data = response.get_json()

    # Проверяем структуру ответа
    assert "move" in data
    assert "top" in data["move"]
    assert "middle" in data["move"]
    assert "bottom" in data["move"]
    assert "discarded" in data["move"]

    # Проверяем содержимое ответа (преобразованное из placement_info)
    assert data["move"]["top"] == [{"rank": "A", "suit": "s"}]
    assert data["move"]["middle"] == [{"rank": "K", "suit": "s"}]
    assert data["move"]["bottom"] == []
    assert data["move"]["discarded"] == "Qs"

    # Проверяем, что choose_placement был вызван один раз с правильными аргументами
    mock_agent_instance.choose_placement.assert_called_once()
    call_args, call_kwargs = mock_agent_instance.choose_placement.call_args
    # call_args[0] - self, call_args[1] - initial_board, call_args[2] - cards_just_dealt, call_args[3] - current_remaining_deck
    assert isinstance(call_args[1], PlayerBoard) # Проверяем тип доски
    assert call_args[2] == [card_as_int, card_ks_int, card_qs_int] # Проверяем карты для размещения
    # Проверяем оставшуюся колоду
    expected_known = {card_as_int, card_ks_int, card_qs_int, Card.from_str("2c"), Card.from_str("3d")}
    expected_remaining = Deck.FULL_DECK_CARDS - expected_known
    assert call_args[3] == expected_remaining # Проверяем колоду

@patch('app.MCTSAgent')
def test_ai_move_no_cards_to_place(MockMCTSAgent, client):
    """Тестирует запрос без карт для размещения."""
    request_data = {
        "selected_cards": [],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [],
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "No valid cards to place" in data["error"]
    MockMCTSAgent.return_value.choose_placement.assert_not_called()

def test_ai_move_invalid_json(client):
    """Тестирует запрос с не-JSON данными."""
    response = client.post('/ai_move', data="not json")
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "Request must be JSON" in data["error"]

def test_ai_move_missing_data(client):
    """Тестирует запрос с отсутствующими полями."""
    response = client.post('/ai_move', json={"selected_cards": ["As"]}) # Нет board
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "Missing or invalid input data" in data["error"]

@patch('app.MCTSAgent')
def test_ai_move_duplicate_cards_hand_board(MockMCTSAgent, client):
    """Тестирует запрос с дубликатами между рукой и доской."""
    request_data = {
        "selected_cards": ["As", "Ks"],
        "board": { "top": ["As", None, None], "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [],
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "Duplicate cards found between hand and board" in data["error"]
    MockMCTSAgent.return_value.choose_placement.assert_not_called()

@patch('app.MCTSAgent')
def test_ai_move_duplicate_cards_hand_discarded(MockMCTSAgent, client):
    """Тестирует запрос с картами в руке, которые уже перманентно удалены."""
    request_data = {
        "selected_cards": ["As", "Ks"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": ["As", "2c"], # As удалена
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "Cards to place are already permanently discarded" in data["error"]
    MockMCTSAgent.return_value.choose_placement.assert_not_called()


@patch('app.MCTSAgent')
def test_ai_move_agent_returns_none(MockMCTSAgent, client):
    """Тестирует случай, когда агент не возвращает размещение."""
    mock_agent_instance = MockMCTSAgent.return_value
    mock_agent_instance.choose_placement.return_value = None # Агент не нашел ход

    request_data = {
        "selected_cards": ["As"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [],
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 200 # Запрос успешен, но ход пустой
    data = response.get_json()
    assert "move" in data
    assert data["move"]["top"] == []
    assert data["move"]["middle"] == []
    assert data["move"]["bottom"] == []
    assert data["move"]["discarded"] is None
    mock_agent_instance.choose_placement.assert_called_once()

@patch('app.MCTSAgent')
def test_ai_move_internal_error(MockMCTSAgent, client):
    """Тестирует обработку неожиданной ошибки в /ai_move."""
    mock_agent_instance = MockMCTSAgent.return_value
    mock_agent_instance.choose_placement.side_effect = Exception("Unexpected MCTS crash")

    request_data = {
        "selected_cards": ["As"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [],
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 500
    data = response.get_json()
    assert "error" in data
    assert "unexpected server error" in data["error"].lower()

# --- Тесты эндпоинта /calculate_score ---

# Мокаем функции оценки для тестов /calculate_score
@patch('app.check_board_foul')
@patch('app.get_row_royalty')
def test_calculate_score_valid_board(mock_get_royalty, mock_check_foul, client):
    """Тестирует /calculate_score для валидной доски."""
    mock_check_foul.return_value = False # Не фол
    # Задаем возвращаемые значения для роялти
    mock_get_royalty.side_effect = lambda cards, row: {
        'top': 9, 'middle': 8, 'bottom': 6
    }.get(row, 0)

    # Создаем валидную полную доску (содержимое не важно, т.к. оценка мокается)
    full_board_data = {
        "top": ["As", "Ad", "2c"],
        "middle": ["Ks", "Kd", "Kh", "Qh", "Qd"],
        "bottom": ["Js", "Jd", "Jh", "Jc", "Ts"]
    }
    response = client.post('/calculate_score', json={"board": full_board_data})

    assert response.status_code == 200
    data = response.get_json()
    assert data["foul"] is False
    assert data["royalties"] == {"top": 9, "middle": 8, "bottom": 6}
    assert data["total_royalty"] == 23 # 9 + 8 + 6
    mock_check_foul.assert_called_once()
    assert mock_get_royalty.call_count == 3 # Вызвана для каждой линии

@patch('app.check_board_foul')
@patch('app.get_row_royalty')
def test_calculate_score_foul_board(mock_get_royalty, mock_check_foul, client):
    """Тестирует /calculate_score для фоловой доски."""
    mock_check_foul.return_value = True # Фол

    full_board_data = { # Содержимое не важно
        "top": ["As", "Ad", "Ac"],
        "middle": ["Ks", "Kd", "2c", "3c", "4c"],
        "bottom": ["Qs", "Qd", "5h", "6h", "7h"]
    }
    response = client.post('/calculate_score', json={"board": full_board_data})

    assert response.status_code == 200
    data = response.get_json()
    assert data["foul"] is True
    assert "royalties" not in data # Роялти не должно быть при фоле
    assert "total_royalty" not in data
    mock_check_foul.assert_called_once()
    mock_get_royalty.assert_not_called() # Роялти не считается при фоле

def test_calculate_score_incomplete_board(client):
    """Тестирует /calculate_score для неполной доски."""
    incomplete_board_data = {
        "top": ["As", None, None],
        "middle": ["Ks", "Kd", None, None, None],
        "bottom": [None] * 5
    }
    response = client.post('/calculate_score', json={"board": incomplete_board_data})
    # Ожидаем фол или ошибку? По коду app.py вернет фол.
    assert response.status_code == 200
    data = response.get_json()
    assert data["foul"] is True
    assert "error" in data
    assert "Board is not complete" in data["error"]

def test_calculate_score_missing_board(client):
    """Тестирует /calculate_score без данных доски."""
    response = client.post('/calculate_score', json={})
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "Missing board data" in data["error"]

# --- Тесты эндпоинтов /update_state и /reset_game_state ---

def test_update_state(client):
    """Тестирует /update_state."""
    state_data = {"board": {"top": ["As", None, None]}, "discarded_cards": ["2c"]}
    response = client.post('/update_state', json=state_data)
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "success"

def test_reset_game_state(client):
    """Тестирует /reset_game_state."""
    response = client.post('/reset_game_state', json={})
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "success"
