# mcts_node.py v2.2 (Heuristic Rollout + RAVE)
"""
Представление узла дерева MCTS для задачи размещения НАБОРА карт OFC Pineapple.
Использует эвристическую симуляцию и RAVE.
"""

import math
import time
import random
import multiprocessing
import traceback
import sys
import logging
from typing import Optional, Any, List, Tuple, Set, Dict, Generator
from itertools import combinations, permutations
from collections import Counter

# Импорты из локальных модулей
try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, MAX_HIGH_CARD_5, RANK_QUEEN, # Добавлены RANK_QUEEN и др.
        get_combination_weight,
        _evaluate_partial_row_potential,
        _get_discard_penalty
    )
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/ofc_evaluators in mcts_node.py: {e}")
    # Заглушки ...
    class PlayerBoard: pass
    class Card: pass
    class Deck: FULL_DECK_CARDS = set()
    def get_row_royalty(*args): return 0
    def check_board_foul(*args): return False
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    WORST_RANK = 9999; WORST_CLASS = 9; MAX_HIGH_CARD_5 = 7462
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {}; STR_RANKS = ""; RANK_QUEEN = 10
    def get_combination_weight(c): return 0.0
    def _evaluate_partial_row_potential(ca, rn): return 0.0
    def _get_discard_penalty(c): return 0.0
    raise ImportError("Missing core logic/evaluator modules for MCTSNode") from e

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING) # Уровень WARNING по умолчанию
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Константы ---
FANTASY_BONUS = 25.0 # Базовый бонус за попадание в ФЛ в симуляции (настроить!)
RAVE_K = 500.0 # Параметр для RAVE beta (настроить!)

# --- Воркер для параллельного роллаута ---
def run_parallel_rollout(board_dict: dict, remaining_deck_ints: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Выполняет один ЭВРИСТИЧЕСКИЙ роллаут из заданного состояния доски.
    Возвращает (итоговое роялти + бонус FL, список сделанных ходов).
    """
    try:
        board = PlayerBoard()
        board.rows = {r: Card.hand_to_int(cards) for r, cards in board_dict.get('rows', {}).items()}
        board._cards_placed = board_dict.get('_cards_placed', 0)
        remaining_deck = set(remaining_deck_ints)
        final_score, actions_history = MCTSNode.heuristic_rollout_simulation(board, remaining_deck)
        return final_score, actions_history
    except Exception as e:
        # Логгируем ошибку в воркере
        print(f"[Worker Error] Error in parallel heuristic rollout: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 0.0, []

# --- Класс MCTSNode ---
class MCTSNode:
    """
    Узел в дереве поиска Монте-Карло (MCTS) для размещения НАБОРА карт OFC.
    Использует эвристическую симуляцию и RAVE.
    """
    def __init__(self,
                 board: PlayerBoard,
                 remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 placement_info: Optional[Dict[str, Any]] = None):
        self.board: PlayerBoard = board
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info # Действие, приведшее сюда
        self.children: Dict[Tuple[Tuple[int, str, int], ...], 'MCTSNode'] = {}
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Optional[int]]]] = None
        self._generated_states_for_expand: Dict[Tuple[Tuple[int, str, int], ...], Tuple[PlayerBoard, Optional[int], Dict[str, Any]]] = {}
        self.visits: int = 0
        self.total_reward: float = 0.0
        # RAVE Статистика
        self.rave_visits: int = 0
        self.rave_reward: float = 0.0

    def is_terminal(self) -> bool:
        return self.board.is_complete()

    def _generate_next_states(self, cards_dealt_for_next_street: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        """
        Генерирует все возможные следующие состояния доски (и сброшенную карту)
        путем размещения карт, РАЗДАННЫХ для СЛЕДУЮЩЕЙ улицы.
        """
        # ... (код функции _generate_next_states без изменений из предыдущей версии) ...
        possible_states_data = []
        self._generated_states_for_expand.clear()

        if self.is_terminal() or not cards_dealt_for_next_street:
            return []

        num_to_place: int; num_to_discard: int
        num_dealt = len(cards_dealt_for_next_street)

        if self.board.get_total_cards() == 0: # Улица 1
            num_to_place = 5; num_to_discard = 0
            if num_dealt != 5: logger.error(f"Generate states: Expected 5 cards for street 1, got {num_dealt}"); return []
        else: # Улицы 2-5
            num_to_place = 2; num_to_discard = 1
            if num_dealt != 3: logger.error(f"Generate states: Expected 3 cards for streets 2-5, got {num_dealt}"); return []

        available_slots = self.board.get_available_slots()
        if len(available_slots) < num_to_place:
            logger.warning(f"Generate states: Not enough slots ({len(available_slots)}) to place {num_to_place} cards.")
            return []

        combo_iterable: Any
        if num_to_discard == 0: # Улица 1
            cards_to_place_tuple = tuple(cards_dealt_for_next_street)
            combo_iterable = [(cards_to_place_tuple, None)]
        else: # Улицы 2-5
            def gen_place_discard_combos():
                for combo in combinations(cards_dealt_for_next_street, num_to_place):
                    discard_list = [c for c in cards_dealt_for_next_street if c not in combo]
                    discard = discard_list[0] if discard_list else None
                    if discard is None: logger.error(f"Could not determine discard card for combo {combo} from {cards_dealt_for_next_street}"); continue
                    yield tuple(combo), discard
            combo_iterable = gen_place_discard_combos()

        for cards_to_place_tuple, current_discarded_card in combo_iterable:
            for slot_combination in combinations(available_slots, num_to_place):
                for card_permutation in permutations(cards_to_place_tuple):
                    try:
                        next_board = self.board.copy()
                        valid_placement = True
                        placements_made: List[Tuple[int, str, int]] = []

                        for i in range(num_to_place):
                            card = card_permutation[i]
                            row, idx = slot_combination[i]
                            if not next_board.add_card(card, row, idx):
                                valid_placement = False; break
                            placements_made.append((card, row, idx))

                        if valid_placement:
                            # Ключ - отсортированный кортеж размещений
                            placement_key = tuple(sorted(placements_made))
                            placement_info = {'placements': placements_made, 'discarded': current_discarded_card}
                            if placement_key not in self._generated_states_for_expand:
                                 self._generated_states_for_expand[placement_key] = (next_board, current_discarded_card, placement_info)
                                 possible_states_data.append((next_board, current_discarded_card))
                    except Exception as e_perm:
                         logger.error(f"Error generating placement permutation: {e_perm}", exc_info=True)

        unique_next_states = list({state_tuple: None for state_tuple in possible_states_data}.keys())
        random.shuffle(unique_next_states)
        return unique_next_states


    def expand(self) -> Optional['MCTSNode']:
        """
        Расширяет узел, выбирая одно неиспробованное СЛЕДУЮЩЕЕ СОСТОЯНИЕ,
        и создавая для него дочерний узел.
        """
        # ... (код функции expand без изменений из предыдущей версии) ...
        if self.is_terminal(): return None
        if self.untried_next_states is None: logger.error("Expand called before _generate_next_states"); return None
        if not self.untried_next_states: return None

        state_to_expand = self.untried_next_states.pop()
        board_state, discarded_card = state_to_expand

        found_key = None
        placement_info = None
        # Ищем ключ и инфо в _generated_states_for_expand по состоянию доски и сбросу
        board_state_tuple = board_state.get_board_state_tuple() # Используем кортеж для сравнения
        for key, (board, discard, info) in self._generated_states_for_expand.items():
             if board.get_board_state_tuple() == board_state_tuple and discard == discarded_card:
                 found_key = key
                 placement_info = info
                 # Не удаляем из словаря здесь, т.к. pop из списка уже убрал состояние
                 break

        if found_key is None or placement_info is None:
             logger.error(f"Could not find matching key/info for state to expand: {state_to_expand}")
             return self.expand() if self.untried_next_states else None

        try:
            # Создаем дочерний узел с информацией о действии, которое к нему привело
            child_node = MCTSNode(
                board=board_state,
                remaining_deck=self.remaining_deck, # Колода та же на момент расширения
                parent=self,
                placement_info=placement_info # Сохраняем действие
            )
            self.children[found_key] = child_node
            return child_node
        except Exception as e:
            logger.error(f"Error during node expansion for state key {found_key}: {e}", exc_info=True)
            return self.expand() if self.untried_next_states else None


    @staticmethod
    def heuristic_rollout_simulation(
            initial_board: PlayerBoard,
            initial_remaining_deck: Set[int]) -> Tuple[float, List[Dict[str, Any]]]:
        """
        Статический метод для выполнения ЭВРИСТИЧЕСКОЙ симуляции (rollout).
        Возвращает (итоговый счет, список сделанных ходов).
        """
        actions_history: List[Dict[str, Any]] = []
        try:
            current_board = initial_board.copy()
            # Используем список для удобства pop, но множество для быстрой проверки наличия
            deck_sim_list = list(initial_remaining_deck)
            random.shuffle(deck_sim_list)
            deck_sim_set = set(deck_sim_list) # Для быстрой проверки

            while not current_board.is_complete():
                num_cards_on_board = current_board.get_total_cards()
                num_to_deal = 3 if num_cards_on_board > 0 else 5
                num_to_place = 2 if num_cards_on_board > 0 else 5

                if len(deck_sim_list) < num_to_deal:
                    logger.debug(f"Heuristic Rollout: Not enough cards ({len(deck_sim_list)}) to deal {num_to_deal}.")
                    return 0.0, actions_history # Возвращаем 0, если не можем доиграть

                # Раздаем карты
                dealt_cards = []
                for _ in range(num_to_deal):
                    card = deck_sim_list.pop()
                    dealt_cards.append(card)
                    deck_sim_set.remove(card) # Удаляем из множества тоже

                placements: List[Tuple[int, str, int]] = []
                discarded_card: Optional[int] = None

                if num_to_place == 5: # Первая улица - УПРОЩЕНИЕ
                    available_slots = current_board.get_available_slots()
                    if len(available_slots) < 5: return 0.0, actions_history
                    # TODO: Реализовать эвристику для 5 карт
                    # Пока размещаем случайно
                    slots_to_use = random.sample(available_slots, 5)
                    placements = []
                    for i in range(5):
                        card = dealt_cards[i]
                        row, idx = slots_to_use[i]
                        if not current_board.add_card(card, row, idx):
                             logger.error("Heuristic Rollout Error: Failed placement on street 1 (random).")
                             return 0.0, actions_history
                        placements.append((card, row, idx))
                    discarded_card = None
                else: # Улицы 2-5
                    placements, discarded_card = MCTSNode._choose_heuristic_placement(current_board, dealt_cards, deck_sim_set) # Передаем колоду для учета
                    # Применяем размещение
                    valid_placement = True
                    for card, row, idx in placements:
                        if not current_board.add_card(card, row, idx):
                            logger.error(f"Heuristic Rollout Error: Failed to place {Card.to_str(card)} in {row}[{idx}].")
                            valid_placement = False; break
                    if not valid_placement: return 0.0, actions_history

                # Сохраняем действие (даже если placements пустые из-за fallback)
                action = {'placements': placements, 'discarded': discarded_card}
                actions_history.append(action)

            # --- Подсчет очков после завершения ---
            is_foul = check_board_foul(current_board)
            if is_foul: return 0.0, actions_history

            total_royalty = 0.0
            for row_name in PlayerBoard.ROW_NAMES:
                row_cards = current_board.get_row_cards(row_name)
                total_royalty += get_row_royalty(row_cards, row_name)

            final_fantasy_bonus = 0.0
            top_row_cards = current_board.get_row_cards("top")
            if len(top_row_cards) == 3:
                 rank_t, class_t, type_t = get_hand_rank_safe(top_row_cards)
                 if rank_t != WORST_RANK:
                     is_fantasy_hand = False
                     if class_t == 6: is_fantasy_hand = True # Trips
                     elif class_t == 8: # Pair
                         ranks = [Card.get_rank_int(c) for c in top_row_cards]
                         pair_rank = next((r for r, count in Counter(ranks).items() if count == 2), -1)
                         if pair_rank >= RANK_QUEEN: is_fantasy_hand = True
                     if is_fantasy_hand: final_fantasy_bonus = FANTASY_BONUS

            final_score = total_royalty + final_fantasy_bonus
            return final_score, actions_history

        except Exception as e:
            logger.error(f"Error during heuristic rollout simulation: {e}", exc_info=True)
            return 0.0, actions_history

    @staticmethod
    def _choose_heuristic_placement(board: PlayerBoard, dealt_cards: List[int], remaining_deck: Set[int]) -> Tuple[List[Tuple[int, str, int]], Optional[int]]:
        """
        Выбирает эвристически 2 карты для размещения и 1 для сброса.
        Учитывает оставшуюся колоду (пока минимально).
        Возвращает (список размещений, карта для сброса).
        """
        available_slots = board.get_available_slots()
        if len(available_slots) < 2 or len(dealt_cards) != 3:
            discard = dealt_cards[0] if dealt_cards else None
            return [], discard # Не можем разместить

        best_option = {'placements': [], 'discard': None, 'score': -float('inf')}

        # Перебираем 3 варианта сброса
        for i in range(3):
            discard_candidate = dealt_cards[i]
            cards_to_place = [dealt_cards[j] for j in range(3) if i != j]
            card_a, card_b = cards_to_place[0], cards_to_place[1]

            current_best_score_for_discard = -float('inf')
            current_best_placements_for_discard = []

            # Перебираем все пары доступных слотов
            for slot_pair in combinations(available_slots, 2):
                slot_a, slot_b = slot_pair
                # Пробуем оба варианта размещения карт по слотам
                for place_perm in [( (card_a, slot_a), (card_b, slot_b) ), ( (card_a, slot_b), (card_b, slot_a) )]:
                    card1, slot1 = place_perm[0]; card2, slot2 = place_perm[1]
                    temp_board = board.copy()
                    valid_placement = True
                    if not temp_board.add_card(card1, slot1[0], slot1[1]): valid_placement = False
                    if valid_placement and not temp_board.add_card(card2, slot2[0], slot2[1]): valid_placement = False

                    if valid_placement:
                        # Оцениваем результат размещения, передаем сброс
                        score = MCTSNode._score_placement_option(temp_board, discard_candidate, remaining_deck)
                        if score > current_best_score_for_discard:
                            current_best_score_for_discard = score
                            current_best_placements_for_discard = [(card1, slot1[0], slot1[1]), (card2, slot2[0], slot2[1])]

            # Сравниваем лучший результат для этого сброса с общим лучшим
            if current_best_score_for_discard > best_option['score']:
                best_option['score'] = current_best_score_for_discard
                best_option['placements'] = current_best_placements_for_discard
                best_option['discard'] = discard_candidate

        # --- Fallback Логика ---
        if not best_option['placements'] or best_option['score'] <= -10000.0: # Если лучший ход - фол
            logger.debug("Heuristic couldn't find a valid non-foul placement. Falling back to random safe.")
            found_safe_fallback = False
            shuffled_discards = list(range(3)); random.shuffle(shuffled_discards)
            shuffled_slots = available_slots[:]; random.shuffle(shuffled_slots)

            for i in shuffled_discards:
                discard_candidate = dealt_cards[i]
                cards_to_place = [dealt_cards[j] for j in range(3) if i != j]
                if len(cards_to_place) < 2: continue

                for slot_pair in combinations(shuffled_slots, 2):
                    slot_a, slot_b = slot_pair
                    # Пробуем оба размещения
                    for place_perm_fb in [( (cards_to_place[0], slot_a), (cards_to_place[1], slot_b) ), ( (cards_to_place[0], slot_b), (cards_to_place[1], slot_a) )]:
                        c1, s1 = place_perm_fb[0]; c2, s2 = place_perm_fb[1]
                        placements = [(c1, s1[0], s1[1]), (c2, s2[0], s2[1])]
                        temp_board = board.copy()
                        valid_temp = True
                        if not temp_board.add_card(c1, s1[0], s1[1]): valid_temp = False
                        if valid_temp and not temp_board.add_card(c2, s2[0], s2[1]): valid_temp = False
                        if valid_temp and not check_board_foul(temp_board):
                             best_option['placements'] = placements
                             best_option['discard'] = discard_candidate
                             best_option['score'] = -1.0 # Даем небольшой отрицательный скор для fallback
                             found_safe_fallback = True; break
                    if found_safe_fallback: break
                if found_safe_fallback: break

            if not found_safe_fallback:
                 logger.warning("Fallback failed to find any non-foul placement.")
                 # Возвращаем пустые placements и первый сброс как худший случай
                 return [], dealt_cards[0] if dealt_cards else None

        return best_option['placements'], best_option['discard']

    @staticmethod
    def _score_placement_option(board: PlayerBoard, discarded_card: Optional[int], remaining_deck: Set[int]) -> float:
        """Оценивает состояние доски ПОСЛЕ гипотетического размещения."""
        total_score = 0.0
        ROW_MULTIPLIERS = {"top": 1.0, "middle": 1.2, "bottom": 1.5}

        if check_board_foul(board):
            return -10000.0 # Огромный штраф за фол

        # Оценка каждого ряда
        for row_name in PlayerBoard.ROW_NAMES:
            cards = board.get_row_cards(row_name)
            row_capacity = PlayerBoard.ROW_CAPACITY[row_name]
            row_score = 0.0
            if not cards: continue

            if len(cards) == row_capacity:
                rank, hand_class, type_str = get_hand_rank_safe(cards)
                if rank != WORST_RANK:
                    combination_weight = get_combination_weight(hand_class)
                    royalty = get_row_royalty(cards, row_name)
                    row_score += combination_weight + royalty * 2.5 # Увеличим вес роялти
                else: row_score -= 100 # Штраф за невалидный ряд
            else:
                # Оценка потенциала неполного ряда
                row_score += _evaluate_partial_row_potential(cards, row_name)
                # TODO: Добавить учет remaining_deck в оценку потенциала

            total_score += row_score * ROW_MULTIPLIERS[row_name]

        # Штраф за сброшенную карту
        if discarded_card is not None:
            total_score -= _get_discard_penalty(discarded_card)

        # Бонус за Фантазию (если топ QQ+ и не фол)
        top_cards = board.get_row_cards('top')
        if len(top_cards) == 3:
            rank_t, class_t, type_t = get_hand_rank_safe(top_cards)
            is_fantasy_hand = False
            if class_t == 6: is_fantasy_hand = True # Trips
            elif class_t == 8: # Pair
                ranks = [Card.get_rank_int(c) for c in top_cards]
                pair_rank = next((r for r, count in Counter(ranks).items() if count == 2), -1)
                if pair_rank >= RANK_QUEEN: is_fantasy_hand = True
            if is_fantasy_hand:
                total_score += FANTASY_BONUS # Добавляем бонус

        return total_score

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        """Выбирает дочерний узел с использованием формулы UCB1 + RAVE."""
        best_score = -float('inf')
        best_child = None

        # Используем посещения родителя для расчета beta и exploration term
        parent_visits = self.visits
        if parent_visits == 0: # Если родитель не посещался, выбрать случайно
             return random.choice(list(self.children.values())) if self.children else None

        parent_visits_log = math.log(parent_visits)

        items = list(self.children.items())
        random.shuffle(items) # Случайность при равных оценках

        # Рассчитываем beta один раз для всех детей
        beta = math.sqrt(RAVE_K / (3 * parent_visits + RAVE_K))

        for placement_key, child in items:
            child_visits = child.visits
            if child_visits == 0:
                # Непосещенные узлы: используем RAVE для инициализации или высокий балл
                if child.rave_visits > 0:
                    rave_score = child.rave_reward / child.rave_visits
                    # Даем высокий базовый балл + RAVE (с весом beta?)
                    score = 1e6 + beta * rave_score + random.random()
                else:
                    score = 1e6 + 10 + random.random() # Еще выше, если и RAVE нет
            else:
                # Посещенные узлы: смешиваем UCB и RAVE
                node_score = child.total_reward / child_visits
                rave_score = child.rave_reward / child.rave_visits if child.rave_visits > 0 else node_score # Fallback RAVE = node_score

                combined_score = (1.0 - beta) * node_score + beta * rave_score
                explore_term = exploration_constant * math.sqrt(parent_visits_log / child_visits)
                score = combined_score + explore_term

            if score > best_score:
                best_score = score
                best_child = child

        if best_child is None and items:
             logger.warning(f"UCT selection resulted in None for node {self}. Choosing random child.")
             best_child = random.choice([c for _, c in items])

        return best_child

    def backpropagate(self, reward: float):
        """Обновляет стандартную статистику узлов вдоль пути."""
        node = self
        while node is not None:
            node.visits += 1
            node.total_reward += reward
            node = node.parent

    def backpropagate_rave(self, simulation_actions: List[Dict[str, Any]], reward: float):
        """Обновляет RAVE статистику узлов вдоль пути (AMAF)."""
        sim_action_keys = set()
        for action in simulation_actions:
            placements = action.get('placements')
            if placements:
                 action_key = tuple(sorted([(p[0], p[1], p[2]) for p in placements]))
                 sim_action_keys.add(action_key)
        if not sim_action_keys: return

        node = self
        while node is not None:
            # Обновляем RAVE для детей этого узла, если их действие было в симуляции
            for child_key, child_node in node.children.items():
                if child_key in sim_action_keys:
                    child_node.rave_visits += 1
                    child_node.rave_reward += reward
            node = node.parent


    def __repr__(self):
        """Строковое представление узла для отладки."""
        q_val = self.total_reward / self.visits if self.visits > 0 else 0.0
        rave_q_val = self.rave_reward / self.rave_visits if self.rave_visits > 0 else 0.0
        action_str = "Root"
        if self.placement_info and self.placement_info.get('placements'):
             p_list = self.placement_info['placements']
             action_str = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_list])
             if self.placement_info.get('discarded'):
                 action_str += f" (D: {Card.to_str(self.placement_info['discarded'])})"

        return (f"[Node V={self.visits} R={q_val:.2f} RV={self.rave_visits} RR={rave_q_val:.2f} "
                f"NChild={len(self.children)} UStates={len(self.untried_next_states or [])} "
                f"Act={action_str}]")
