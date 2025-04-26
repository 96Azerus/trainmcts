# app.py v1.0
"""
Основной файл веб-приложения Flask для режима тренировки OFC Pineapple.
Обрабатывает HTTP-запросы, вызывает MCTS AI для получения оптимального
размещения карт и отдает HTML-страницу.
"""

import os
import json
import traceback
import sys
import logging
import time
from typing import Optional, Dict, Any, Tuple, List, Set

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

# Импорты из локальных модулей
try:
    from ofc_logic import Card, Deck, PlayerBoard, CARD_PLACEHOLDER, INVALID_CARD
    from mcts_agent import MCTSAgent
except ImportError as e:
    print(f"FATAL ERROR: Import failed from local modules: {e}", file=sys.stderr)
    print("Ensure the script is run from the project root directory.", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
except Exception as e_global:
     print(f"FATAL ERROR during initial imports: {e_global}", file=sys.stderr)
     traceback.print_exc(file=sys.stderr)
     sys.exit(1)

# --- Инициализация приложения и логирования ---
load_dotenv() # Загружает переменные из .env файла
app = Flask(__name__, template_folder='templates') # Указываем папку шаблонов

# Настройка логирования
log_level_str = os.environ.get('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(module)s:%(lineno)d] - %(message)s')
# Используем логгер Flask по умолчанию, но настраиваем его
app.logger.handlers.clear() # Очищаем стандартные обработчики
stream_handler = logging.StreamHandler(sys.stdout) # Вывод в stdout для Render/Docker
stream_handler.setFormatter(log_formatter)
stream_handler.setLevel(log_level) # Уровень для обработчика
app.logger.addHandler(stream_handler)
app.logger.setLevel(log_level) # Уровень для логгера
# Уменьшаем шум от Werkzeug
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app.logger.info("--- Flask App Initialization ---")

# Конфигурация Flask
# Секретный ключ не нужен, так как сессии не используются в этом режиме
is_production = os.environ.get('RENDER') == 'true' or os.environ.get('FLASK_ENV') == 'production'
app.logger.info(f"Production mode: {is_production}")

# --- Вспомогательные функции ---

def parse_cards_data(card_strs: List[Optional[str]]) -> List[int]:
    """Парсит список строк карт в список int, игнорируя невалидные."""
    valid_ints: List[int] = []
    if not isinstance(card_strs, list):
        app.logger.warning("Invalid card data format: expected list.")
        return []
    for card_str in card_strs:
        if card_str and card_str != CARD_PLACEHOLDER:
            try:
                card_int = Card.from_str(card_str)
                if card_int != INVALID_CARD and card_int > 0:
                    valid_ints.append(card_int)
            except (ValueError, TypeError):
                app.logger.warning(f"Invalid card string '{card_str}' received, skipping.")
    return valid_ints

def parse_board_data(board_data: Dict[str, List[Optional[str]]]) -> Tuple[PlayerBoard, Set[int]]:
    """Парсит данные доски из JSON в объект PlayerBoard и возвращает набор карт на доске."""
    board = PlayerBoard()
    board_cards_set: Set[int] = set()
    if not isinstance(board_data, dict):
        app.logger.warning("Invalid board data format: expected dict.")
        return board, board_cards_set

    for row_name in PlayerBoard.ROW_NAMES:
        row_strs = board_data.get(row_name, [])
        if not isinstance(row_strs, list):
            app.logger.warning(f"Invalid row data format for '{row_name}': expected list.")
            continue
        capacity = PlayerBoard.ROW_CAPACITY.get(row_name, 0)
        for i in range(capacity):
            card_str = row_strs[i] if i < len(row_strs) else None
            if card_str and card_str != CARD_PLACEHOLDER:
                try:
                    card_int = Card.from_str(card_str)
                    if card_int != INVALID_CARD and card_int > 0:
                        if not board.add_card(card_int, row_name, i):
                            app.logger.warning(f"Failed to add card {card_str} to board at {row_name}[{i}] during parsing.")
                        else:
                            board_cards_set.add(card_int)
                except (ValueError, TypeError):
                     app.logger.warning(f"Invalid card string '{card_str}' in board data, skipping.")
    return board, board_cards_set

# --- Маршруты Flask ---
app.logger.info("Defining Flask routes...")

@app.route('/')
def index():
    """Отдает главную HTML страницу тренировки."""
    app.logger.info("Route / called (GET)")
    try:
        return render_template('training.html')
    except Exception as e:
        app.logger.error(f"Error rendering template: {e}", exc_info=True)
        return "Error loading page.", 500

@app.route('/ai_move', methods=['POST'])
def get_ai_move():
    """
    Принимает текущее состояние доски и карты для размещения,
    возвращает последовательность оптимальных размещений от MCTS AI.
    """
    start_request_time = time.time()
    app.logger.info("Route /ai_move called (POST)")
    if not request.is_json:
        app.logger.warning("Request is not JSON")
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    app.logger.debug(f"Received data: {data}")

    # --- Валидация и парсинг входных данных ---
    selected_card_strs = data.get('selected_cards')
    board_data = data.get('board')
    ai_settings = data.get('ai_settings', {})

    if not isinstance(selected_card_strs, list) or not board_data:
        app.logger.warning("Missing or invalid 'selected_cards' or 'board' data")
        return jsonify({"error": "Missing or invalid input data"}), 400

    try:
        cards_to_place_ints = parse_cards_data(selected_card_strs)
        board, board_cards_set = parse_board_data(board_data)
    except Exception as e_parse:
        app.logger.error(f"Error parsing input data: {e_parse}", exc_info=True)
        return jsonify({"error": f"Error parsing input: {e_parse}"}), 400

    if not cards_to_place_ints:
        app.logger.warning("No valid cards to place provided.")
        return jsonify({"error": "No valid cards to place"}), 400

    # Проверка на дубликаты между рукой и доской
    hand_cards_set = set(cards_to_place_ints)
    if not hand_cards_set.isdisjoint(board_cards_set):
        duplicates = hand_cards_set.intersection(board_cards_set)
        app.logger.warning(f"Duplicate cards found between hand and board: {[Card.to_str(c) for c in duplicates]}")
        return jsonify({"error": "Duplicate cards found between hand and board"}), 400

    # --- Определение оставшейся колоды ---
    known_cards = board_cards_set.union(hand_cards_set)
    remaining_deck_set = Deck.FULL_DECK_CARDS - known_cards
    if len(known_cards) + len(remaining_deck_set) != 52:
        app.logger.error(f"Card accounting error: Known({len(known_cards)}) + Remaining({len(remaining_deck_set)}) != 52")
        # Попытка восстановить колоду
        remaining_deck_set = Deck.FULL_DECK_CARDS - known_cards
        app.logger.warning(f"Re-calculated remaining deck size: {len(remaining_deck_set)}")


    # --- Настройка и запуск MCTS ---
    try:
        # Настройки MCTS из запроса или по умолчанию
        mcts_time_limit = int(ai_settings.get('aiTime', 5)) * 1000 # Время в мс
        mcts_simulations = int(ai_settings.get('iterations', 10000)) # Используем iterations как max_simulations
        # num_workers пока не используем напрямую в настройках агента, но можно добавить
        # rollouts_per_leaf тоже можно добавить

        # Ограничиваем время и симуляции разумными пределами
        mcts_time_limit = max(100, min(mcts_time_limit, 60000)) # 0.1с - 60с
        # max_simulations пока не используется напрямую агентом, он работает по времени

        agent = MCTSAgent(time_limit_ms=mcts_time_limit) # Используем только лимит времени

        placements_result: List[Dict[str, Any]] = []
        current_board_sim = board.copy() # Копируем доску для симуляции ходов
        current_cards_to_place = list(cards_to_place_ints) # Копия
        current_remaining_deck = set(remaining_deck_set) # Копия

        app.logger.info(f"Starting iterative MCTS for {len(current_cards_to_place)} cards...")

        # Итеративно выбираем лучший ход для каждой карты
        while current_cards_to_place:
            if current_board_sim.is_complete():
                app.logger.warning("Board became complete during iterative MCTS, stopping.")
                break

            best_action = agent.choose_action(
                current_board_sim,
                current_cards_to_place,
                current_remaining_deck
            )

            if best_action:
                card_int, row_name, index = best_action
                app.logger.info(f"MCTS recommended action: {Card.to_str(card_int)} to {row_name}[{index}]")

                # Применяем действие к симулируемой доске
                if not current_board_sim.add_card(card_int, row_name, index):
                    app.logger.error(f"Internal error: Failed to apply recommended action {best_action} to simulation board.")
                    # Пытаемся продолжить со следующей картой, но это плохой знак
                    current_cards_to_place.pop(0) # Удаляем карту, которую не смогли разместить
                    continue

                # Добавляем результат
                placements_result.append({
                    "card": Card.to_str(card_int),
                    "row": row_name,
                    "index": index
                })

                # Обновляем состояние для следующей итерации
                current_cards_to_place.pop(0) # Удаляем размещенную карту
                # current_remaining_deck не меняется, т.к. карты были из руки

            else:
                app.logger.warning("MCTS agent returned None action. Stopping iterative placement.")
                # Если агент не смог выбрать ход, прерываем цикл
                break

        request_duration = time.time() - start_request_time
        app.logger.info(f"AI move request processed in {request_duration:.3f}s. Placements found: {len(placements_result)}")

        # --- Формирование ответа ---
        # В режиме тренировки мы возвращаем только последовательность размещений
        response_data = {"placements": placements_result}
        return jsonify(response_data)

    except Exception as e:
        app.logger.error(f"Unexpected Error during /ai_move: {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred."}), 500


# --- Запуск приложения ---
if __name__ == '__main__':
    app.logger.info("--- Starting Main Execution ---")
    # Используем порт из окружения или 10000 по умолчанию (как в Dockerfile)
    port = int(os.environ.get('PORT', 10000))
    # Debug mode включаем только если не в продакшене и FLASK_DEBUG=1/true
    debug_mode = os.environ.get('FLASK_DEBUG', '0').lower() in ['true', '1', 'yes'] and not is_production
    if debug_mode:
        app.logger.setLevel(logging.DEBUG)
        for handler in app.logger.handlers: handler.setLevel(logging.DEBUG)
        app.logger.info("Flask debug mode is ON.")
    else:
        app.logger.info("Flask debug mode is OFF.")

    app.logger.info(f"Starting Flask app server on host 0.0.0.0, port {port}")
    # use_reloader=False важно при запуске через Gunicorn
    app.run(host='0.0.0.0', port=port, debug=debug_mode, use_reloader=debug_mode)
    app.logger.info("--- Flask App Exiting ---")
