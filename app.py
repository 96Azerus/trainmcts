# app.py v2.4 (Handles "??" discard marker, AI subset placement logic, refined response)
"""
Основной файл веб-приложения Flask для режима тренировки OFC Pineapple.
Обрабатывает HTTP-запросы, вызывает MCTS AI для получения оптимального
размещения НАБОРА карт и отдает HTML-страницу.
Добавлена обработка маркера "??" для неизвестно сброшенных карт.
Логика AI теперь сама разбирается с размещением подмножества карт.
Формат ответа /ai_move уточнен для передачи точных позиций карт.
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

try:
    from ofc_logic import Card, Deck, PlayerBoard, CARD_PLACEHOLDER, INVALID_CARD, INT_RANK_TO_CHAR, INT_SUIT_TO_CHAR, UNKNOWN_CARD_MARKER_LOGIC
    from mcts_agent import MCTSAgent, HEURISTIC_FOUL_PENALTY # Импортируем HEURISTIC_FOUL_PENALTY
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

load_dotenv()
app = Flask(__name__, template_folder='templates')

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

app.logger.info("--- Flask App Initialization (v2.4) ---")
is_production = os.environ.get('RENDER') == 'true' or os.environ.get('FLASK_ENV') == 'production'
app.logger.info(f"Production mode: {is_production}")

def parse_cards_data(card_data: Any, for_discard_pile: bool = False) -> Tuple[List[int], int]:
    valid_ints: List[int] = []
    unknown_count: int = 0
    if not isinstance(card_data, list):
        app.logger.warning(f"Invalid card data format: expected list, got {type(card_data)}.")
        return [], 0
    for item in card_data:
        if item is None: continue
        if for_discard_pile and isinstance(item, str) and item == UNKNOWN_CARD_MARKER_LOGIC:
            unknown_count += 1; continue
        card_str: Optional[str] = None
        if isinstance(item, str): card_str = item
        elif isinstance(item, dict) and 'rank' in item and 'suit' in item:
            rank = item['rank']; suit_char = item['suit']
            suit_map_inv = {'♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}
            suit = suit_map_inv.get(suit_char, suit_char).lower()
            if rank == "10": rank = "T" # Конвертация для бэкенда
            if rank and suit: card_str = str(rank) + str(suit)
            else: app.logger.warning(f"Invalid rank/suit in card object: {item}")
        else: app.logger.warning(f"Unexpected item type in card data: {type(item)}, value: {item}")
        if card_str and card_str != CARD_PLACEHOLDER:
            try:
                card_int = Card.from_str(card_str)
                if card_int != INVALID_CARD and card_int > 0: valid_ints.append(card_int)
                else: app.logger.warning(f"Parsed invalid card int from '{card_str}': {card_int}")
            except (ValueError, TypeError): app.logger.warning(f"Invalid card string '{card_str}' received, skipping.")
    return valid_ints, unknown_count

def parse_board_data(board_data: Any) -> Tuple[PlayerBoard, Set[int]]:
    board = PlayerBoard(); board_cards_set: Set[int] = set()
    if not isinstance(board_data, dict): app.logger.warning(f"Invalid board data: expected dict, got {type(board_data)}."); return board, board_cards_set
    for row_name in PlayerBoard.ROW_NAMES:
        row_items = board_data.get(row_name, [])
        if not isinstance(row_items, list): app.logger.warning(f"Invalid row data for '{row_name}': expected list, got {type(row_items)}."); continue
        capacity = PlayerBoard.ROW_CAPACITY.get(row_name, 0)
        for i in range(capacity):
             card_item = row_items[i] if i < len(row_items) else None
             if card_item is None: continue
             card_int: Optional[int] = None
             if isinstance(card_item, str) and card_item != CARD_PLACEHOLDER:
                 try: card_int = Card.from_str(card_item) # Card.from_str ожидает "Ts", а не "10s"
                 except (ValueError, TypeError): pass
             elif isinstance(card_item, dict):
                 temp_list, _ = parse_cards_data([card_item])
                 if temp_list: card_int = temp_list[0]
             if card_int and card_int != INVALID_CARD and card_int > 0:
                 if not board.add_card(card_int, row_name, i): app.logger.warning(f"Failed to add {Card.to_str(card_int)} to board at {row_name}[{i}].")
                 else: board_cards_set.add(card_int)
    return board, board_cards_set

app.logger.info("Defining Flask routes...")
@app.route('/')
def index():
    app.logger.info("Route / called (GET)")
    try:
        return render_template('training.html')
    except Exception as e:
        app.logger.error(f"Error rendering template: {e}", exc_info=True)
        return "Error loading page.", 500

@app.route('/ai_move', methods=['POST'])
def get_ai_move():
    start_request_time = time.time()
    app.logger.info("Route /ai_move called (POST)")
    if not request.is_json:
        app.logger.warning("Request not JSON")
        return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    app.logger.debug(f"Received data: {data}")
    selected_cards_data = data.get('selected_cards') # Карты из combination_area (только известные)
    board_data = data.get('board')
    discarded_cards_data = data.get('discarded_cards', []) # Карты, сброшенные пользователем (включая "??" маркеры)
    ai_settings = data.get('ai_settings', {})

    if not isinstance(selected_cards_data, list) or not board_data:
        app.logger.warning("Missing 'selected_cards' or 'board' data")
        return jsonify({"error": "Missing or invalid input data"}), 400
    try:
        cards_to_place_ints, _ = parse_cards_data(selected_cards_data, for_discard_pile=False)
        board, board_cards_set = parse_board_data(board_data)
        known_perm_discard_list, num_unknown_markers = parse_cards_data(discarded_cards_data, for_discard_pile=True)
        known_perm_discard_set = set(known_perm_discard_list)
    except Exception as e_parse:
        app.logger.error(f"Error parsing input: {e_parse}", exc_info=True)
        return jsonify({"error": f"Error parsing input: {e_parse}"}), 400

    if not cards_to_place_ints and board.get_total_cards() < PlayerBoard.TOTAL_CAPACITY:
        app.logger.info("No valid cards provided by user to place by AI, and board is not full.")
        return jsonify({"move": {"placements_details": [], "discarded": None}, "message": "No valid cards provided to place."})

    hand_cards_set = set(cards_to_place_ints)
    err_msgs = []
    if board_cards_set.intersection(hand_cards_set):
        err_msgs.append(f"Duplicates hand-board: {[Card.to_str(c) for c in board_cards_set.intersection(hand_cards_set)]}")
    if known_perm_discard_set.intersection(hand_cards_set):
        err_msgs.append(f"Duplicates hand-known_discard: {[Card.to_str(c) for c in known_perm_discard_set.intersection(hand_cards_set)]}")
    if known_perm_discard_set.intersection(board_cards_set):
        err_msgs.append(f"Duplicates board-known_discard: {[Card.to_str(c) for c in known_perm_discard_set.intersection(board_cards_set)]}")
    if err_msgs:
        app.logger.warning(". ".join(err_msgs))
        return jsonify({"error": ". ".join(err_msgs)}), 400

    num_unknown_removed_total = num_unknown_markers
    remaining_deck_for_ai = Deck.FULL_DECK_CARDS - board_cards_set - hand_cards_set - known_perm_discard_set
    app.logger.info(f"Deck for AI: Full({len(Deck.FULL_DECK_CARDS)}) - Board({len(board_cards_set)}) - Hand({len(hand_cards_set)}) - KnownDiscard({len(known_perm_discard_set)}) = {len(remaining_deck_for_ai)} cards.")
    app.logger.info(f"Unknown cards removed (markers): {num_unknown_removed_total}")

    all_distinct_known_cards = board_cards_set.union(hand_cards_set).union(known_perm_discard_set)
    if len(all_distinct_known_cards) + len(remaining_deck_for_ai) + num_unknown_removed_total != 52:
         app.logger.error(
             f"CARD ACCOUNTING ERROR: DistinctKnown({len(all_distinct_known_cards)})"
             f" + AI_Deck({len(remaining_deck_for_ai)})"
             f" + UnknownRemoved({num_unknown_removed_total)})"
             f" = {len(all_distinct_known_cards) + len(remaining_deck_for_ai) + num_unknown_removed_total} != 52."
         )

    try:
        mcts_time_limit = int(ai_settings.get('aiTime', 5)) * 1000
        mcts_time_limit = max(100, min(mcts_time_limit, 60000))
        agent = MCTSAgent(time_limit_ms=mcts_time_limit)
        app.logger.info(f"Starting MCTS for {len(cards_to_place_ints)} cards (board has {board.get_total_cards()})...")

        best_placement_info = agent.choose_placement(
            initial_board=board, cards_just_dealt=cards_to_place_ints,
            current_remaining_deck=remaining_deck_for_ai, num_unknown_removed_cards=num_unknown_removed_total
        )
        req_dur = time.time() - start_request_time

        if best_placement_info:
            app.logger.info(f"AI move in {req_dur:.3f}s. Placement: {best_placement_info}")
            
            response_placements_details = []
            if best_placement_info.get('placements'):
                 for p_card_int, p_row, p_idx in best_placement_info['placements']:
                     try:
                         rank_char = INT_RANK_TO_CHAR.get(Card.get_rank_int(p_card_int))
                         suit_int = Card.get_suit_int(p_card_int)
                         suit_char = INT_SUIT_TO_CHAR.get(suit_int)
                         if rank_char and suit_char:
                              suit_map_to_symbol = {'s': '♠', 'h': '♥', 'd': '♦', 'c': '♣'}
                              suit_symbol = suit_map_to_symbol.get(suit_char, '?')
                              response_placements_details.append({
                                  "rank": rank_char, "suit": suit_symbol, 
                                  "row": p_row, "index": p_idx
                               })
                         else:
                             app.logger.warning(f"Could not format card_int {p_card_int} for placement response.")
                     except Exception as e_fmt_p:
                         app.logger.warning(f"Error formatting card_int {p_card_int} for placement: {e_fmt_p}")
            
            response_discarded_formatted = None
            discard_from_ai = best_placement_info.get('discarded')
            if discard_from_ai is not None:
                if isinstance(discard_from_ai, tuple):
                    response_discarded_formatted = [Card.to_str(c) for c in discard_from_ai]
                else:
                    response_discarded_formatted = Card.to_str(discard_from_ai)

            final_response_data = {
                "move": {
                    "placements_details": response_placements_details,
                    "discarded": response_discarded_formatted
                }
            }
            app.logger.debug(f"Sending (new format) response data: {final_response_data}")
            return jsonify(final_response_data)

        else:
            app.logger.warning(f"AI agent did not return a placement after {req_dur:.3f}s.")
            return jsonify({"move": {"placements_details": [], "discarded": None}, "message": "AI could not find a placement."})

    except Exception as e:
        app.logger.error(f"Unexpected Error during /ai_move: {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred."}), 500

@app.route('/calculate_score', methods=['POST'])
def calculate_score():
    start_request_time = time.time()
    app.logger.info("Route /calculate_score called (POST)")
    if not request.is_json:
        app.logger.warning("Request not JSON")
        return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    board_data = data.get('board')
    if not board_data:
        app.logger.warning("Missing 'board' data")
        return jsonify({"error": "Missing board data"}), 400
    try:
        board, _ = parse_board_data(board_data)
        if not board.is_complete():
             app.logger.warning(f"Score for incomplete board ({board.get_total_cards()}/13).")
             return jsonify({"foul": True, "error": "Board is not complete", "royalties": {}, "total_royalty": 0})
        is_foul = check_board_foul(board)
        app.logger.info(f"Foul check: {is_foul}")
        if is_foul:
            return jsonify({"foul": True, "royalties": {}, "total_royalty": 0})
        else:
            royalties = {}
            total_royalty = 0
            for row_name in PlayerBoard.ROW_NAMES:
                royalty = get_row_royalty(board.get_row_cards(row_name), row_name)
                royalties[row_name] = royalty
                total_royalty += royalty
            app.logger.info(f"Royalties: {royalties}, Total: {total_royalty}")
            app.logger.info(f"Score calc in {time.time() - start_request_time:.3f}s.")
            return jsonify({"foul": False, "royalties": royalties, "total_royalty": total_royalty})
    except Exception as e:
        app.logger.error(f"Error in /calculate_score: {e}", exc_info=True)
        return jsonify({"error": "Server error in score calc."}), 500

@app.route('/update_state', methods=['POST'])
def update_state():
    app.logger.debug("Route /update_state called (POST)")
    if not request.is_json:
        app.logger.warning("/update_state: Not JSON")
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400
    data = request.get_json()
    app.logger.debug(f"State update data: {data}")
    return jsonify({"status": "success", "message": "State received"})

@app.route('/reset_game_state', methods=['POST'])
def reset_game_state():
    app.logger.info("Route /reset_game_state called (POST)")
    return jsonify({"status": "success", "message": "Server state reset acknowledged"})

if __name__ == '__main__':
    app.logger.info("--- Starting Main Execution (v2.4) ---")
    port = int(os.environ.get('PORT', 10000))
    debug_mode = os.environ.get('FLASK_DEBUG', '0').lower() in ['true', '1', 'yes'] and not is_production
    if debug_mode:
        app.logger.setLevel(logging.DEBUG)
        [h.setLevel(logging.DEBUG) for h in app.logger.handlers]
        app.logger.info("Flask debug mode ON.")
    else:
        app.logger.info("Flask debug mode OFF.")
    app.logger.info(f"Starting Flask app server on 0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=debug_mode, use_reloader=debug_mode)
    app.logger.info("--- Flask App Exiting ---")
