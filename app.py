# app.py v2.2 (Refactored for Set Placement MCTS, Parsing Fix, Response Format Fix)
"""
Основной файл веб-приложения Flask для режима тренировки OFC Pineapple.
Обрабатывает HTTP-запросы, вызывает MCTS AI для получения оптимального
размещения НАБОРА карт и отдает HTML-страницу.
Исправлен парсинг None значений.
Исправлено форматирование ответа /ai_move.
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
    # Добавляем INT_RANK_TO_CHAR и INT_SUIT_TO_CHAR
    from ofc_logic import Card, Deck, PlayerBoard, CARD_PLACEHOLDER, INVALID_CARD, INT_RANK_TO_CHAR, INT_SUIT_TO_CHAR
    from mcts_agent import MCTSAgent
    from ofc_evaluators import check_board_foul, get_row_royalty, WORST_RANK
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
load_dotenv()
app = Flask(__name__, template_folder='templates')

# Настройка логирования
log_level_str = os.environ.get('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(module)s:%(lineno)d] - %(message)s')
app.logger.handlers.clear()
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)
stream_handler.setLevel(log_level)
app.logger.addHandler(stream_handler)
app.logger.setLevel(log_level)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app.logger.info("--- Flask App Initialization (v2.2) ---")

# Конфигурация Flask
is_production = os.environ.get('RENDER') == 'true' or os.environ.get('FLASK_ENV') == 'production'
app.logger.info(f"Production mode: {is_production}")

# --- Вспомогательные функции ---

def parse_cards_data(card_data: Any) -> List[int]:
    """Парсит данные карт (строки или объекты) в список int, игнорируя невалидные."""
    valid_ints: List[int] = []
    if not isinstance(card_data, list):
        app.logger.warning(f"Invalid card data format: expected list, got {type(card_data)}.")
        return []

    for item in card_data:
        card_str: Optional[str] = None
        # --- ИСПРАВЛЕНО: Пропускаем None элементы ---
        if item is None:
            continue

        if isinstance(item, str):
            card_str = item
        elif isinstance(item, dict) and 'rank' in item and 'suit' in item:
            rank = item['rank']
            suit_char = item['suit']
            suit_map_inv = {'♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}
            suit = suit_map_inv.get(suit_char, suit_char).lower()
            if rank and suit:
                 card_str = str(rank) + str(suit)
            else:
                 app.logger.warning(f"Invalid rank/suit in card object: {item}")
        else:
             app.logger.warning(f"Unexpected item type in card data: {type(item)}, value: {item}")

        if card_str and card_str != CARD_PLACEHOLDER:
            try:
                card_int = Card.from_str(card_str)
                if card_int != INVALID_CARD and card_int > 0:
                    valid_ints.append(card_int)
                else:
                     app.logger.warning(f"Parsed invalid card int from '{card_str}': {card_int}")
            except (ValueError, TypeError):
                app.logger.warning(f"Invalid card string '{card_str}' received, skipping.")
    return valid_ints

def parse_board_data(board_data: Any) -> Tuple[PlayerBoard, Set[int]]:
    """Парсит данные доски из JSON в объект PlayerBoard и возвращает набор карт на доске."""
    board = PlayerBoard()
    board_cards_set: Set[int] = set()
    if not isinstance(board_data, dict):
        app.logger.warning(f"Invalid board data format: expected dict, got {type(board_data)}.")
        return board, board_cards_set

    for row_name in PlayerBoard.ROW_NAMES:
        row_items = board_data.get(row_name, [])
        if not isinstance(row_items, list):
            app.logger.warning(f"Invalid row data format for '{row_name}': expected list, got {type(row_items)}.")
            continue

        capacity = PlayerBoard.ROW_CAPACITY.get(row_name, 0)
        for i in range(capacity):
             card_item = row_items[i] if i < len(row_items) else None
             # --- ИСПРАВЛЕНО: Пропускаем None слоты ---
             if card_item is None:
                 continue

             card_int: Optional[int] = None
             if isinstance(card_item, str) and card_item != CARD_PLACEHOLDER:
                 try: card_int = Card.from_str(card_item)
                 except (ValueError, TypeError): pass
             elif isinstance(card_item, dict):
                 temp_list = parse_cards_data([card_item])
                 if temp_list: card_int = temp_list[0]

             if card_int and card_int != INVALID_CARD and card_int > 0:
                 if not board.add_card(card_int, row_name, i):
                     app.logger.warning(f"Failed to add card {Card.to_str(card_int)} to board at {row_name}[{i}] during parsing (slot occupied?).")
                 else:
                     board_cards_set.add(card_int)

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
    Принимает текущее состояние доски, карты для размещения и перманентно удаленные карты.
    Возвращает ОДНО оптимальное размещение для НАБОРА карт от MCTS AI.
    """
    start_request_time = time.time()
    app.logger.info("Route /ai_move called (POST)")
    if not request.is_json:
        app.logger.warning("Request is not JSON")
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    app.logger.debug(f"Received data: {data}")

    selected_cards_data = data.get('selected_cards')
    board_data = data.get('board')
    discarded_cards_data = data.get('discarded_cards', [])
    ai_settings = data.get('ai_settings', {})

    if not isinstance(selected_cards_data, list) or not board_data:
        app.logger.warning("Missing or invalid 'selected_cards' or 'board' data")
        return jsonify({"error": "Missing or invalid input data"}), 400

    try:
        cards_to_place_ints = parse_cards_data(selected_cards_data)
        board, board_cards_set = parse_board_data(board_data)
        permanently_discarded_ints = set(parse_cards_data(discarded_cards_data))
    except Exception as e_parse:
        app.logger.error(f"Error parsing input data: {e_parse}", exc_info=True)
        return jsonify({"error": f"Error parsing input: {e_parse}"}), 400

    if not cards_to_place_ints:
        app.logger.warning("No valid cards to place provided.")
        return jsonify({"error": "No valid cards to place"}), 400

    hand_cards_set = set(cards_to_place_ints)
    known_on_board_or_hand = board_cards_set.union(hand_cards_set)
    duplicates_board_hand = board_cards_set.intersection(hand_cards_set)
    duplicates_discard_hand = permanently_discarded_ints.intersection(hand_cards_set)
    duplicates_discard_board = permanently_discarded_ints.intersection(board_cards_set)

    if duplicates_board_hand:
        app.logger.warning(f"Duplicate cards found between hand and board: {[Card.to_str(c) for c in duplicates_board_hand]}")
        return jsonify({"error": "Duplicate cards found between hand and board"}), 400
    if duplicates_discard_hand:
        app.logger.warning(f"Cards to place are already permanently discarded: {[Card.to_str(c) for c in duplicates_discard_hand]}")
        return jsonify({"error": "Cards to place are already permanently discarded"}), 400
    if duplicates_discard_board:
         app.logger.warning(f"Cards on board are also permanently discarded: {[Card.to_str(c) for c in duplicates_discard_board]}")

    all_known_cards = known_on_board_or_hand.union(permanently_discarded_ints)
    remaining_deck_set = Deck.FULL_DECK_CARDS - all_known_cards
    actual_total = len(all_known_cards) + len(remaining_deck_set)
    if actual_total != 52:
        app.logger.error(f"Card accounting error: Known({len(all_known_cards)}) + Remaining({len(remaining_deck_set)}) = {actual_total} != 52. "
                         f"Board({len(board_cards_set)}), Hand({len(hand_cards_set)}), Discarded({len(permanently_discarded_ints)}).")
        remaining_deck_set = Deck.FULL_DECK_CARDS - all_known_cards
        app.logger.warning(f"Re-calculated remaining deck size: {len(remaining_deck_set)}")

    try:
        mcts_time_limit = int(ai_settings.get('aiTime', 5)) * 1000
        mcts_time_limit = max(100, min(mcts_time_limit, 60000))
        agent = MCTSAgent(time_limit_ms=mcts_time_limit)
        app.logger.info(f"Starting MCTS placement search for {len(cards_to_place_ints)} cards...")

        best_placement_info: Optional[Dict[str, Any]] = agent.choose_placement(
            initial_board=board,
            cards_just_dealt=cards_to_place_ints,
            current_remaining_deck=remaining_deck_set
        )
        request_duration = time.time() - start_request_time

        if best_placement_info:
            app.logger.info(f"AI move request processed in {request_duration:.3f}s. Best placement found.")
            response_placements = []
            if best_placement_info.get('placements'):
                 response_placements = [
                     {"card_int": p[0], "row": p[1], "index": p[2]} # Сохраняем int для форматирования
                     for p in best_placement_info['placements']
                 ]
            response_discarded = None
            if best_placement_info.get('discarded'):
                 response_discarded = Card.to_str(best_placement_info['discarded'])

            response_data = {
                "move": {
                    "top": [], "middle": [], "bottom": [],
                    "discarded": response_discarded
                }
            }
            for p in response_placements:
                row_key = p['row']
                if row_key in response_data['move']:
                     try:
                         card_int = p['card_int']
                         # --- ИСПРАВЛЕНО: Используем импортированные словари ---
                         rank_char = INT_RANK_TO_CHAR.get(Card.get_rank_int(card_int))
                         suit_char = INT_SUIT_TO_CHAR.get(Card.get_suit_int(card_int))
                         if rank_char and suit_char:
                              # --- ИСПРАВЛЕНО: Формируем объект с rank/suit ---
                              # Важно: фронтенд ожидает символ масти, а не букву
                              suit_map_to_symbol = {'s': '♠', 'h': '♥', 'd': '♦', 'c': '♣'}
                              suit_symbol = suit_map_to_symbol.get(suit_char, '?')
                              response_data['move'][row_key].append({"rank": rank_char, "suit": suit_symbol})
                         else:
                              app.logger.warning(f"Could not format card int {card_int} for response.")
                     except Exception as e_format:
                          app.logger.warning(f"Error formatting card int {p.get('card_int', 'N/A')} for response: {e_format}")

            app.logger.debug(f"Sending response data: {response_data}")
            return jsonify(response_data)
        else:
            app.logger.warning(f"AI agent did not return a placement after {request_duration:.3f}s.")
            return jsonify({"move": {"top": [], "middle": [], "bottom": [], "discarded": None}})

    except Exception as e:
        app.logger.error(f"Unexpected Error during /ai_move: {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred."}), 500

# --- Эндпоинт /calculate_score ---
@app.route('/calculate_score', methods=['POST'])
def calculate_score():
    start_request_time = time.time()
    app.logger.info("Route /calculate_score called (POST)")
    if not request.is_json:
        app.logger.warning("Request is not JSON")
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    board_data = data.get('board')

    if not board_data:
        app.logger.warning("Missing 'board' data for score calculation")
        return jsonify({"error": "Missing board data"}), 400

    try:
        board, board_cards_set = parse_board_data(board_data)

        if not board.is_complete():
             app.logger.warning(f"Score calculation requested for incomplete board ({board.get_total_cards()}/13).")
             return jsonify({"foul": True, "error": "Board is not complete"})

        is_foul = check_board_foul(board)
        app.logger.info(f"Foul check result: {is_foul}")

        if is_foul:
            return jsonify({"foul": True})
        else:
            royalties = {}
            total_royalty = 0
            for row_name in PlayerBoard.ROW_NAMES:
                row_cards = board.get_row_cards(row_name)
                royalty = get_row_royalty(row_cards, row_name)
                royalties[row_name] = royalty
                total_royalty += royalty

            app.logger.info(f"Calculated royalties: {royalties}, Total: {total_royalty}")
            request_duration = time.time() - start_request_time
            app.logger.info(f"Score calculation request processed in {request_duration:.3f}s.")

            return jsonify({
                "foul": False,
                "royalties": royalties,
                "total_royalty": total_royalty
            })

    except Exception as e:
        app.logger.error(f"Error during /calculate_score: {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred during score calculation."}), 500

# --- Эндпоинт /update_state ---
@app.route('/update_state', methods=['POST'])
def update_state():
    app.logger.debug("Route /update_state called (POST)")
    if not request.is_json:
        app.logger.warning("/update_state: Request is not JSON")
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400
    data = request.get_json()
    app.logger.debug(f"Received state update data: {data}")
    return jsonify({"status": "success", "message": "State received"})

# --- Эндпоинт /reset_game_state ---
@app.route('/reset_game_state', methods=['POST'])
def reset_game_state():
    app.logger.info("Route /reset_game_state called (POST)")
    return jsonify({"status": "success", "message": "Server state reset acknowledged"})

# --- Запуск приложения ---
if __name__ == '__main__':
    app.logger.info("--- Starting Main Execution (v2.2) ---")
    port = int(os.environ.get('PORT', 10000))
    debug_mode = os.environ.get('FLASK_DEBUG', '0').lower() in ['true', '1', 'yes'] and not is_production
    if debug_mode:
        app.logger.setLevel(logging.DEBUG)
        for handler in app.logger.handlers: handler.setLevel(logging.DEBUG)
        app.logger.info("Flask debug mode is ON.")
    else:
        app.logger.info("Flask debug mode is OFF.")

    app.logger.info(f"Starting Flask app server on host 0.0.0.0, port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug_mode, use_reloader=debug_mode)
    app.logger.info("--- Flask App Exiting ---")
