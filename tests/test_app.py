# tests/test_app.py v2.5 (Updated for new API response format)
"""
Интеграционные тесты для Flask приложения app.py.
Обновлены для работы с choose_placement и новым API.
Добавлены тесты для /calculate_score, /update_state, /reset_game_state.
Исправлена проверка аргументов в test_ai_move_valid_request с использованием assert_called_once_with и именованных аргументов.
"""

import pytest
import json
from unittest.mock import patch, MagicMock, ANY

# Импортируем Flask app и зависимости
try:
    from app import app as flask_app
    from ofc_logic import Card, PlayerBoard, CARD_PLACEHOLDER, Deck
    from mcts_agent import MCTSAgent
    from ofc_evaluators import check_board_foul, get_row_royalty
except ImportError:
    pytest.skip("Skipping app tests due to missing imports", allow_module_level=True)

# --- Фикстура для Flask test client ---
@pytest.fixture
def client():
    """Создает тестовый клиент Flask."""
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        yield client

# --- Тесты эндпоинта / ---
def test_index_route(client):
    """Тестирует GET запрос к корневому маршруту."""
    response = client.get('/')
    assert response.status_code == 200
    assert b"<title>OFC Training Mode</title>" in response.data

# --- Тесты эндпоинта /ai_move ---

@patch('app.MCTSAgent')
def test_ai_move_valid_request(MockMCTSAgent, client):
    """Тестирует валидный POST запрос к /ai_move с новой логикой."""
    mock_agent_instance = MockMCTSAgent.return_value
    card_as_int = Card.from_str('As')
    card_ks_int = Card.from_str('Ks')
    card_qs_int = Card.from_str('Qs')
    
    # Мок ответа от MCTS агента
    mock_placement_info = {
        'placements': [(card_as_int, 'top', 0), (card_ks_int, 'middle', 0)],
        'discarded': card_qs_int
    }
    mock_agent_instance.choose_placement.return_value = mock_placement_info

    request_data = {
        "selected_cards": ["As", "Ks", "Qs"],
        "board": {"top": [None]*3, "middle": [None]*5, "bottom": [None]*5},
        "discarded_cards": ["2c", "3d"],
        "ai_settings": {"aiTime": 1}
    }

    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 200
    data = response.get_json()

    # FIX: Проверяем новый, более структурированный формат ответа
    assert "move" in data
    assert "placements_details" in data["move"]
    assert "discarded" in data["move"]
    
    expected_placements = [
        {"rank": "A", "suit": "♠", "row": "top", "index": 0},
        {"rank": "K", "suit": "♠", "row": "middle", "index": 0}
    ]
    # Сортируем для независимости от порядка
    assert sorted(data["move"]["placements_details"], key=lambda x: x['rank']) == sorted(expected_placements, key=lambda x: x['rank'])
    assert data["move"]["discarded"] == "Qs"

    # FIX: Проверка аргументов через assert_called_once_with с именованными аргументами
    board_cards = set() # Пустая доска
    discarded_cards = {Card.from_str("2c"), Card.from_str("3d")}
    hand_cards = {card_as_int, card_ks_int, card_qs_int}
    
    expected_known = board_cards.union(discarded_cards).union(hand_cards)
    expected_remaining = Deck.FULL_DECK_CARDS - expected_known
    expected_cards_dealt = [card_as_int, card_ks_int, card_qs_int]

    mock_agent_instance.choose_placement.assert_called_once_with(
        initial_board=ANY,
        cards_just_dealt=expected_cards_dealt,
        current_remaining_deck=expected_remaining,
        num_unknown_removed_cards=0 # Проверяем, что этот аргумент передается
    )
    
    call_kwargs = mock_agent_instance.choose_placement.call_args.kwargs
    assert isinstance(call_kwargs.get('initial_board'), PlayerBoard)


@patch('app.MCTSAgent')
def test_ai_move_no_cards_to_place(MockMCTSAgent, client):
    request_data = {
        "selected_cards": [],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [], "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 200 # Теперь это не ошибка, а просто сообщение
    data = response.get_json(); assert "message" in data and "No valid cards" in data["message"]
    MockMCTSAgent.return_value.choose_placement.assert_not_called()

def test_ai_move_invalid_json(client):
    response = client.post('/ai_move', data="not json")
    assert response.status_code == 400
    data = response.get_json(); assert "error" in data and "Request must be JSON" in data["error"]

def test_ai_move_missing_data(client):
    response = client.post('/ai_move', json={"selected_cards": ["As"]})
    assert response.status_code == 400
    data = response.get_json(); assert "error" in data and "Missing or invalid input data" in data["error"]

@patch('app.MCTSAgent')
def test_ai_move_duplicate_cards_hand_board(MockMCTSAgent, client):
    request_data = {
        "selected_cards": ["As", "Ks"],
        "board": { "top": ["As", None, None], "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [], "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 400
    data = response.get_json(); assert "error" in data and "Duplicates hand-board" in data["error"]
    MockMCTSAgent.return_value.choose_placement.assert_not_called()

@patch('app.MCTSAgent')
def test_ai_move_duplicate_cards_hand_discarded(MockMCTSAgent, client):
    request_data = {
        "selected_cards": ["As", "Ks"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": ["As", "2c"], "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 400
    data = response.get_json(); assert "error" in data and "Duplicates hand-known_discard" in data["error"]
    MockMCTSAgent.return_value.choose_placement.assert_not_called()

@patch('app.MCTSAgent')
def test_ai_move_agent_returns_none(MockMCTSAgent, client):
    mock_agent_instance = MockMCTSAgent.return_value
    mock_agent_instance.choose_placement.return_value = None
    request_data = {
        "selected_cards": ["As"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [], "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 200
    data = response.get_json(); assert "move" in data
    assert data["move"]["placements_details"] == []
    assert data["move"]["discarded"] is None
    mock_agent_instance.choose_placement.assert_called_once()

@patch('app.MCTSAgent')
def test_ai_move_internal_error(MockMCTSAgent, client):
    mock_agent_instance = MockMCTSAgent.return_value
    mock_agent_instance.choose_placement.side_effect = Exception("Unexpected MCTS crash")
    request_data = {
        "selected_cards": ["As"],
        "board": { "top": [None]*3, "middle": [None]*5, "bottom": [None]*5 },
        "discarded_cards": [], "ai_settings": {}
    }
    response = client.post('/ai_move', json=request_data)
    assert response.status_code == 500
    data = response.get_json(); assert "error" in data and "unexpected server error" in data["error"].lower()

# --- Тесты эндпоинта /calculate_score ---
@patch('app.check_board_foul')
@patch('app.get_row_royalty')
def test_calculate_score_valid_board(mock_get_royalty, mock_check_foul, client):
    mock_check_foul.return_value = False
    mock_get_royalty.side_effect = lambda cards, row: {'top': 9, 'middle': 8, 'bottom': 6}.get(row, 0)
    full_board_data = {
        "top": ["As", "Ad", "2c"], "middle": ["Ks", "Kd", "Kh", "Qh", "Qd"], "bottom": ["Js", "Jd", "Jh", "Jc", "Ts"]
    }
    response = client.post('/calculate_score', json={"board": full_board_data})
    assert response.status_code == 200
    data = response.get_json()
    assert data["foul"] is False
    assert data["royalties"] == {"top": 9, "middle": 8, "bottom": 6}
    assert data["total_royalty"] == 23
    mock_check_foul.assert_called_once()
    assert mock_get_royalty.call_count == 3

@patch('app.check_board_foul')
@patch('app.get_row_royalty')
def test_calculate_score_foul_board(mock_get_royalty, mock_check_foul, client):
    mock_check_foul.return_value = True
    full_board_data = {
        "top": ["As", "Ad", "Ac"], "middle": ["Ks", "Kd", "2c", "3c", "4c"], "bottom": ["Qs", "Qd", "5h", "6h", "7h"]
    }
    response = client.post('/calculate_score', json={"board": full_board_data})
    assert response.status_code == 200
    data = response.get_json()
    assert data["foul"] is True
    assert data["royalties"] == {}
    assert data["total_royalty"] == 0
    mock_check_foul.assert_called_once()
    mock_get_royalty.assert_not_called()

def test_calculate_score_incomplete_board(client):
    incomplete_board_data = {
        "top": ["As", None, None], "middle": ["Ks", "Kd", None, None, None], "bottom": [None] * 5
    }
    response = client.post('/calculate_score', json={"board": incomplete_board_data})
    assert response.status_code == 200
    data = response.get_json()
    assert data["foul"] is True
    assert "error" in data and "Board is not complete" in data["error"]

def test_calculate_score_missing_board(client):
    response = client.post('/calculate_score', json={})
    assert response.status_code == 400
    data = response.get_json(); assert "error" in data and "Missing board data" in data["error"]

# --- Тесты эндпоинтов /update_state и /reset_game_state ---
def test_update_state(client):
    state_data = {"board": {"top": ["As", None, None]}, "discarded_cards": ["2c"]}
    response = client.post('/update_state', json=state_data)
    assert response.status_code == 200
    data = response.get_json(); assert data["status"] == "success"

def test_reset_game_state(client):
    response = client.post('/reset_game_state', json={})
    assert response.status_code == 200
    data = response.get_json(); assert data["status"] == "success"
