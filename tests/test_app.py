# tests/test_app.py v1.0
"""
Интеграционные тесты для Flask приложения app.py.
"""

import pytest
import json
from unittest.mock import patch, MagicMock

# Импортируем Flask app и зависимости
# Оборачиваем в try-except на случай проблем с импортом в тестовой среде
try:
    from app import app as flask_app # Импортируем как flask_app, чтобы не конфликтовать
    from ofc_logic import Card, PlayerBoard, CARD_PLACEHOLDER
except ImportError:
    pytest.skip("Skipping app tests due to missing imports (app or ofc_logic)", allow_module_level=True)

# --- Фикстура для Flask test client ---
@pytest.fixture
def client():
    """Создает тестовый клиент Flask."""
    flask_app.config['TESTING'] = True
    # Отключаем логирование во время тестов, если нужно
    # flask_app.logger.setLevel(logging.CRITICAL)
    with flask_app.test_client() as client:
        yield client

# --- Тесты эндпоинта / ---
def test_index_route(client):
    """Тестирует GET запрос к корневому маршруту."""
    response = client.get('/')
    assert response.status_code == 200
    assert b"<title>OFC Training Mode</title>" in response.data # Проверяем наличие заголовка

# --- Тесты эндпоинта /ai_move ---

@patch('app.MCTSAgent') # Мокаем класс MCTSAgent внутри app.py
def test_ai_move_valid_request(MockMCTSAgent, client):
    """Тестирует валидный POST запрос к /ai_move."""
    # Настройка мока агента
    mock_agent_instance = MockMCTSAgent.return_value
    # Моделируем, что агент возвращает последовательность действий
    action1 = (Card.from_str('As'), 'top', 0)
    action2 = (Card.from_str('Ks'), 'top', 1)
    mock_agent_instance.choose_action.side_effect = [action1, action2, None] # Возвращаем два действия, потом None

    # Данные запроса
    request_data = {
        "selected_cards": ["As", "Ks"],
        "board": {
            "top": [None, None, None],
            "middle": [None] * 5,
            "bottom": [None] * 5
        },
        "ai_settings": {"aiTime": 1} # Время в секундах
    }

    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 200
    data = response.get_json()
    assert "placements" in data
    assert isinstance(data["placements"], list)
    # Ожидаем два размещения
    assert len(data["placements"]) == 2
    assert data["placements"][0] == {"card": "As", "row": "top", "index": 0}
    assert data["placements"][1] == {"card": "Ks", "row": "top", "index": 1}
    # Проверяем, что choose_action был вызван дважды
    assert mock_agent_instance.choose_action.call_count == 2

@patch('app.MCTSAgent')
def test_ai_move_no_cards_to_place(MockMCTSAgent, client):
    """Тестирует запрос без карт для размещения."""
    request_data = {
        "selected_cards": [], # Пустой список
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "No valid cards to place" in data["error"]
    MockMCTSAgent.return_value.choose_action.assert_not_called()

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
def test_ai_move_duplicate_cards(MockMCTSAgent, client):
    """Тестирует запрос с дубликатами между рукой и доской."""
    request_data = {
        "selected_cards": ["As", "Ks"],
        "board": {
            "top": ["As", None, None], # Дубликат As
            "middle": [None] * 5,
            "bottom": [None] * 5
        },
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert "Duplicate cards found" in data["error"]
    MockMCTSAgent.return_value.choose_action.assert_not_called()

@patch('app.MCTSAgent')
def test_ai_move_agent_returns_none(MockMCTSAgent, client):
    """Тестирует случай, когда агент не возвращает действие."""
    mock_agent_instance = MockMCTSAgent.return_value
    mock_agent_instance.choose_action.return_value = None # Агент не нашел ход

    request_data = {
        "selected_cards": ["As"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 200 # Запрос успешен, но размещений нет
    data = response.get_json()
    assert "placements" in data
    assert data["placements"] == [] # Ожидаем пустой список размещений
    mock_agent_instance.choose_action.assert_called_once()

@patch('app.MCTSAgent')
def test_ai_move_internal_error(MockMCTSAgent, client):
    """Тестирует обработку неожиданной ошибки в /ai_move."""
    mock_agent_instance = MockMCTSAgent.return_value
    mock_agent_instance.choose_action.side_effect = Exception("Unexpected MCTS crash") # Моделируем ошибку

    request_data = {
        "selected_cards": ["As"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 500 # Ожидаем ошибку сервера
    data = response.get_json()
    assert "error" in data
    assert "unexpected server error" in data["error"].lower()
