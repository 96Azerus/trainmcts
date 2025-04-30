# mcts_node.py v1.2
"""
Представление узла дерева MCTS для задачи размещения карт OFC Pineapple.
Работает с состоянием доски и картами для размещения.
Цель - максимизация роялти.
Импорты check_board_foul и get_row_royalty изменены на ofc_evaluators.
Импорт evaluator_5card изменен на ofc_evaluator_5card.
"""

import math
import time
import random
import multiprocessing
import traceback
import sys
import logging
from typing import Optional, Any, List, Tuple, Set, Dict

# Импорты из локальных модулей
try:
    from ofc_logic import PlayerBoard, Card, Deck
    # Импортируем функции скоринга и 3-card эвалюатор из ofc_evaluators
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK,
        evaluate_3_card_ofc,
        check_board_foul, get_row_royalty
    )
    # --- ИСПРАВЛЕНО: Импортируем 5-card эвалюатор напрямую ---
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/ofc_evaluators/ofc_evaluator_5card in mcts_node.py: {e}")
    # Заглушки, чтобы код мог быть проанализирован
    class PlayerBoard: pass
    class Card: pass
    class Deck: FULL_DECK_CARDS = set()
    def get_row_royalty(*args): return 0
    def check_board_foul(*args): return False
    def get_hand_rank_safe(*args): return (9999, "Invalid")
    WORST_RANK = 9999
    def evaluate_3_card_ofc(*args): return (999, "Error", "ERR")
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    # Перевыбрасываем ошибку, т.к. без этих модулей работа невозможна
    raise ImportError("Missing core logic/evaluator modules for MCTSNode") from e


# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)

# --- Воркер для параллельного роллаута ---
def run_parallel_rollout(board_dict: dict, cards_to_place_ints: List[int], remaining_deck_ints: List[int]) -> float:
    """
    Выполняет один роллаут из заданного состояния в отдельном процессе.
    Возвращает итоговое роялти.
    """
    try:
        # Пересоздаем зависимости внутри воркера
        # (Импорты уже сделаны на уровне модуля mcts_node)
        # Создаем объекты из переданных данных
        board = PlayerBoard()
        board.rows = {r: Card.hand_to_int(cards) for r, cards in board_dict.get('rows', {}).items()}
        board._cards_placed = board_dict.get('_cards_placed', 0)
        board.is_foul = board_dict.get('is_foul', False)

        cards_to_place = list(cards_to_place_ints)
        remaining_deck = set(remaining_deck_ints)

        # Выполняем симуляцию
        final_royalty = MCTSNode.static_rollout_simulation(board, cards_to_place, remaining_deck)
        return final_royalty
    except Exception as e:
        # Логгируем ошибку внутри воркера
        # Используем print, так как настройка logging может быть сложной в дочерних процессах
        print(f"[Worker Error] Error in parallel rollout: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 0.0 # Возвращаем 0 роялти при ошибке

# --- Класс MCTSNode ---
class MCTSNode:
    """ Узел в дереве поиска Монте-Карло (MCTS) для размещения карт OFC. """

    def __init__(self,
                 board: PlayerBoard,
                 cards_to_place: List[int],
                 remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 action: Optional[Tuple[int, str, int]] = None):
        """
        Инициализирует узел MCTS.

        Args:
            board (PlayerBoard): Текущее состояние доски.
            cards_to_place (List[int]): Список карт (int), которые нужно разместить.
            remaining_deck (Set[int]): Множество карт (int), оставшихся в колоде (не на доске и не в cards_to_place).
            parent (Optional['MCTSNode']): Родительский узел.
            action (Optional[Tuple[int, str, int]]): Действие (карта, ряд, индекс), которое привело к этому узлу.
        """
        self.board: PlayerBoard = board # Не копируем здесь для производительности, копируем при expand
        self.cards_to_place: List[int] = cards_to_place
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.action: Optional[Tuple[int, str, int]] = action # (card_int, row_name, index)

        self.children: Dict[Tuple[int, str, int], 'MCTSNode'] = {}
        self.untried_actions: Optional[List[Tuple[int, str, int]]] = None # Список возможных размещений *следующей* карты

        self.visits: int = 0
        self.total_reward: float = 0.0 # Суммарное роялти из симуляций

        # RAVE убран для упрощения v1.0

    def is_terminal(self) -> bool:
        """Проверяет, является ли узел терминальным (все 13 карт размещены)."""
        return self.board.is_complete()

    def _get_available_placements(self) -> List[Tuple[int, str, int]]:
        """Генерирует возможные действия (размещение одной карты) из текущего узла."""
        if not self.cards_to_place or self.is_terminal():
            return []

        card_to_place = self.cards_to_place[0] # Берем первую карту для размещения
        available_slots = self.board.get_available_slots()

        possible_actions = []
        for row_name, index in available_slots:
            action = (card_to_place, row_name, index)
            possible_actions.append(action)

        return possible_actions

    def expand(self) -> Optional['MCTSNode']:
        """
        Расширяет узел, выбирая одно неиспробованное действие (размещение карты),
        применяя его и создавая дочерний узел.
        """
        if self.is_terminal():
            logger.debug("Expand called on terminal node.")
            return None

        # Инициализируем/получаем неиспробованные действия
        if self.untried_actions is None:
            self.untried_actions = self._get_available_placements()
            random.shuffle(self.untried_actions)

        if not self.untried_actions:
            # logger.debug("No untried actions left to expand.")
            return None

        # Выбираем следующее действие
        action_to_expand = self.untried_actions.pop()
        card_int, row_name, index = action_to_expand

        # Создаем новое состояние доски
        try:
            next_board = self.board.copy()
            if not next_board.add_card(card_int, row_name, index):
                # Это не должно происходить, если _get_available_placements работает верно
                logger.error(f"Failed to apply expansion action {action_to_expand} to copied board.")
                return self.expand() # Пробуем следующее действие

            # Обновляем список карт для размещения и колоду
            next_cards_to_place = self.cards_to_place[1:] # Убираем размещенную карту
            # Колода остается той же, т.к. карта была из 'cards_to_place'

            # Создаем дочерний узел
            child_node = MCTSNode(
                board=next_board,
                cards_to_place=next_cards_to_place,
                remaining_deck=self.remaining_deck, # Колода не меняется
                parent=self,
                action=action_to_expand
            )
            self.children[action_to_expand] = child_node
            return child_node

        except Exception as e:
            logger.error(f"Error during node expansion for action {action_to_expand}: {e}", exc_info=True)
            return self.expand() # Пробуем следующее действие при ошибке

    @staticmethod
    def static_rollout_simulation(
            initial_board: PlayerBoard,
            initial_cards_to_place: List[int],
            initial_remaining_deck: Set[int]) -> float:
        """
        Статический метод для выполнения симуляции (rollout).
        Достраивает доску до 13 карт и возвращает итоговое роялти.
        """
        try:
            current_board = initial_board.copy()
            cards_to_place_sim = list(initial_cards_to_place) # Копия
            deck_sim = list(initial_remaining_deck) # Преобразуем в список для random.sample
            random.shuffle(deck_sim) # Перемешиваем один раз

            cards_needed = PlayerBoard.TOTAL_CAPACITY - current_board.get_total_cards()
            cards_available_hand = len(cards_to_place_sim)
            cards_available_deck = len(deck_sim)

            # 1. Размещаем карты из 'руки' (cards_to_place_sim)
            num_to_place_from_hand = min(cards_needed, cards_available_hand)
            if num_to_place_from_hand > 0:
                slots_for_hand = current_board.get_available_slots()
                if len(slots_for_hand) < num_to_place_from_hand:
                    logger.warning("Rollout: Not enough slots for hand cards.")
                    return 0.0 # Не можем разместить, считаем 0 роялти
                # Размещаем случайным образом
                placements = random.sample(slots_for_hand, num_to_place_from_hand)
                for i in range(num_to_place_from_hand):
                    card = cards_to_place_sim[i]
                    row, idx = placements[i]
                    if not current_board.add_card(card, row, idx):
                        logger.warning(f"Rollout: Failed to place hand card {Card.to_str(card)} in slot {row}[{idx}].")
                        # Можно вернуть 0 или продолжить с тем, что есть
                cards_needed -= num_to_place_from_hand

            # 2. Размещаем карты из 'колоды' (deck_sim), если нужно
            num_to_place_from_deck = min(cards_needed, cards_available_deck)
            if num_to_place_from_deck > 0:
                slots_for_deck = current_board.get_available_slots()
                if len(slots_for_deck) < num_to_place_from_deck:
                    logger.warning("Rollout: Not enough slots for deck cards.")
                    return 0.0
                # Берем карты с конца перемешанной колоды
                cards_from_deck = deck_sim[-num_to_place_from_deck:]
                placements = random.sample(slots_for_deck, num_to_place_from_deck)
                for i in range(num_to_place_from_deck):
                    card = cards_from_deck[i]
                    row, idx = placements[i]
                    if not current_board.add_card(card, row, idx):
                        logger.warning(f"Rollout: Failed to place deck card {Card.to_str(card)} in slot {row}[{idx}].")

            # 3. Считаем роялти для итоговой доски
            if not current_board.is_complete():
                logger.warning(f"Rollout finished with incomplete board ({current_board.get_total_cards()}/13).")
                return 0.0

            # Проверяем фол (используем импортированную функцию)
            is_foul = check_board_foul(current_board) # Убраны эвалюаторы
            if is_foul:
                return 0.0 # Роялти за фол = 0

            # Считаем роялти (используем импортированную функцию)
            total_royalty = 0
            for row_name in PlayerBoard.ROW_NAMES:
                row_cards = current_board.get_row_cards(row_name)
                # Убраны эвалюаторы из вызова
                total_royalty += get_row_royalty(row_cards, row_name)

            return float(total_royalty)

        except Exception as e:
            logger.error(f"Error during static rollout simulation: {e}", exc_info=True)
            return 0.0

    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        """Выбирает дочерний узел с использованием формулы UCB1."""
        best_score = -float('inf')
        best_child = None

        # Используем логарифм от посещений родителя + 1 для избежания log(0)
        parent_visits_log = math.log(self.visits + 1)
        children_items = list(self.children.items())
        if not children_items: return None

        random.shuffle(children_items) # Для случайного выбора при равных очках

        for action, child in children_items:
            if child.visits == 0:
                score = float('inf') # Не посещался - максимальный приоритет
            else:
                # Q-value (среднее роялти)
                exploit_term = child.total_reward / child.visits
                # Exploration term
                explore_term = exploration_constant * math.sqrt(parent_visits_log / child.visits)
                score = exploit_term + explore_term

            if score > best_score:
                best_score = score
                best_child = child
            elif score == best_score and score != float('inf'): # Случайный выбор при равных
                 if random.choice([True, False]): best_child = child

        # Если все потомки имеют score = -inf (маловероятно), выбираем случайно
        if best_child is None: best_child = random.choice([c for _, c in children_items])

        return best_child

    def backpropagate(self, reward: float):
        """Обновляет статистику узлов вдоль пути."""
        node = self
        while node is not None:
            node.visits += 1
            node.total_reward += reward
            node = node.parent

    def __repr__(self):
        """Строковое представление узла для отладки."""
        q_val = self.total_reward / self.visits if self.visits > 0 else 0.0
        action_str = "Root"
        if self.action:
            card_s, row, idx = self.action
            action_str = f"{Card.to_str(card_s)}@{row}[{idx}]"

        return (f"[Act:{action_str} V={self.visits} R={q_val:.2f} "
                f"NChild={len(self.children)} UAct={len(self.untried_actions or [])}]")
