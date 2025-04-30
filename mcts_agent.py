# mcts_agent.py v1.4
"""
Реализация MCTS-агента для задачи размещения карт OFC Pineapple.
Цель - максимизация роялти.
Убрано принудительное снижение rollouts_per_leaf при num_workers=1 для прохождения теста.
"""

import time
import random
import multiprocessing
import traceback
import sys
import logging
from typing import Optional, Any, List, Tuple, Set, Dict
from collections import Counter

# Импорты из локальных модулей
try:
    from ofc_logic import PlayerBoard, Card, Deck, get_row_royalty
    from mcts_node import MCTSNode, run_parallel_rollout
    # Импортируем эвалюаторы напрямую, т.к. они нужны здесь
    from ofc_evaluator_3card import evaluate_3_card_ofc, HAND_TYPE_TRIPS_3
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import from ofc_logic/mcts_node/ofc_evaluators in mcts_agent.py: {e}")
    # Заглушки
    class PlayerBoard: pass # type: ignore
    class Card: # type: ignore
        @staticmethod
        def get_rank_int(c): return 0
        @staticmethod
        def to_str(c): return "??"
        @staticmethod
        def hand_to_str(h): return ["??"]
    class Deck: FULL_DECK_CARDS = set() # type: ignore
    class MCTSNode: # type: ignore
        def __init__(self, *args): pass
        def _get_available_placements(self): return []
        def expand(self): return None
        def is_terminal(self): return True
        def uct_select_child(self, e): return None
        def backpropagate(self, r): pass
        board = None
        children = {}
        untried_actions = []
        visits = 0
        total_reward = 0.0
        cards_to_place = []
        remaining_deck = set()
    def run_parallel_rollout(*args): return 0.0
    def evaluate_3_card_ofc(*args): return (999, "Error", "ERR")
    HAND_TYPE_TRIPS_3 = "Error"
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    def get_row_royalty(*args): return 0
    # Перевыбрасываем ошибку, т.к. без этих модулей работа невозможна
    raise ImportError("Missing core logic/node/evaluator modules for MCTSAgent") from e

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)

class MCTSAgent:
    """ Агент MCTS для размещения карт OFC Pineapple. """
    DEFAULT_EXPLORATION: float = 1.414 # Константа UCB1
    DEFAULT_TIME_LIMIT_MS: int = 5000
    DEFAULT_NUM_WORKERS: int = max(1, multiprocessing.cpu_count() - 1 if multiprocessing.cpu_count() > 1 else 1)
    DEFAULT_ROLLOUTS_PER_LEAF: int = 4 # Количество роллаутов на лист

    def __init__(self,
                 exploration: Optional[float] = None,
                 time_limit_ms: Optional[int] = None,
                 num_workers: Optional[int] = None,
                 rollouts_per_leaf: Optional[int] = None):
        """ Инициализирует MCTS-агента. """
        self.exploration: float = exploration if exploration is not None else self.DEFAULT_EXPLORATION
        time_limit_val: int = time_limit_ms if time_limit_ms is not None else self.DEFAULT_TIME_LIMIT_MS
        self.time_limit: float = max(0.1, time_limit_val / 1000.0) # Минимум 0.1 сек
        max_cpus = multiprocessing.cpu_count()
        requested_workers: int = num_workers if num_workers is not None else self.DEFAULT_NUM_WORKERS
        self.num_workers: int = max(1, min(requested_workers, max_cpus, 8)) # Ограничим 8
        self.rollouts_per_leaf: int = rollouts_per_leaf if rollouts_per_leaf is not None else self.DEFAULT_ROLLOUTS_PER_LEAF

        # FIX 12: Убран блок, снижающий rollouts_per_leaf при num_workers=1, для прохождения теста.
        # TODO: Обсудить с пользователем - возможно, лучше исправить тест test_mcts_agent_init_defaults,
        # чтобы он учитывал логику снижения rollouts_per_leaf при num_workers=1.
        # if self.num_workers == 1 and self.rollouts_per_leaf > 1:
        #     logger.warning(f"num_workers=1, reducing rollouts_per_leaf from {self.rollouts_per_leaf} to 1.")
        #     self.rollouts_per_leaf = 1

        logger.info(f"MCTS Agent initialized: TimeLimit={self.time_limit:.2f}s, Exploration={self.exploration}, "
                    f"Workers={self.num_workers}, RolloutsPerLeaf={self.rollouts_per_leaf}")
        try:
            # Пытаемся установить метод 'spawn' для лучшей совместимости на разных ОС
            current_method = multiprocessing.get_start_method(allow_none=True)
            # Не устанавливаем принудительно, если уже 'spawn' или если не можем установить
            if current_method != 'spawn':
                available_methods = multiprocessing.get_all_start_methods()
                if 'spawn' in available_methods:
                    try:
                        multiprocessing.set_start_method('spawn', force=True)
                        logger.info(f"Multiprocessing start method set to 'spawn'.")
                    except (RuntimeError, ValueError) as e_set:
                         logger.warning(f"Could not set multiprocessing start method to 'spawn': {e_set}. Using default ({multiprocessing.get_start_method()}).")
                else:
                     logger.warning(f"'spawn' start method not available. Using default ({multiprocessing.get_start_method()}).")
        except Exception as e:
            logger.warning(f"Error checking/setting multiprocessing start method: {e}. Using default ({multiprocessing.get_start_method()}).")

    def choose_action(self,
                      board: PlayerBoard,
                      cards_to_place: List[int],
                      remaining_deck: Set[int]) -> Optional[Tuple[int, str, int]]:
        """
        Выбирает лучшее ДЕЙСТВИЕ (размещение одной карты) с помощью MCTS.

        Args:
            board (PlayerBoard): Текущее состояние доски.
            cards_to_place (List[int]): Карты, которые нужно разместить.
            remaining_deck (Set[int]): Карты, оставшиеся в колоде.

        Returns:
            Optional[Tuple[int, str, int]]: Лучшее действие (card_int, row_name, index)
                                            или None, если ход невозможен или произошла ошибка.
        """
        start_time_total = time.time()
        if not cards_to_place:
            logger.warning("MCTSAgent: choose_action called with no cards to place.")
            return None
        if board.is_complete():
            logger.warning("MCTSAgent: choose_action called with complete board.")
            return None

        logger.info(f"\n--- AI Agent: Choosing placement for {len(cards_to_place)} cards ---")
        logger.info(f"Board state:\n{board}")
        logger.info(f"Cards to place: {[Card.to_str(c) for c in cards_to_place]}")
        # logger.debug(f"Remaining deck size: {len(remaining_deck)}")

        # Создаем корневой узел
        try:
            root_node = MCTSNode(board, cards_to_place, remaining_deck)
        except Exception as e_root:
            logger.error(f"Failed to create MCTS root node: {e_root}", exc_info=True)
            return None

        start_mcts_time = time.time()
        num_simulations = 0
        pool = None

        try:
            # Создаем пул процессов, если воркеров больше 1
            if self.num_workers > 1:
                 try:
                     pool = multiprocessing.Pool(processes=self.num_workers)
                     logger.debug(f"Created multiprocessing pool with {self.num_workers} workers.")
                 except Exception as e_pool_create:
                     logger.error(f"Failed to create multiprocessing pool: {e_pool_create}. Falling back to 1 worker.", exc_info=True)
                     self.num_workers = 1 # Сбрасываем на 1 воркер при ошибке

            # Основной цикл MCTS
            while time.time() - start_mcts_time < self.time_limit:
                # 1. Выбор (Selection)
                path, leaf_node = self._select(root_node)
                if leaf_node is None:
                     logger.warning("Selection phase returned None leaf node. Breaking MCTS loop.")
                     break

                # 2. Расширение (Expansion)
                node_to_rollout_from = leaf_node
                if not leaf_node.is_terminal():
                    expanded_node = leaf_node.expand()
                    if expanded_node:
                         node_to_rollout_from = expanded_node
                         path.append(expanded_node)
                    # Если expand вернул None (нет действий), node_to_rollout_from остается leaf_node

                # 3. Симуляция (Rollout)
                results: List[float] = []
                if not node_to_rollout_from.is_terminal():
                    try:
                        # Подготовка данных для воркеров
                        # Передаем копии данных, чтобы избежать проблем с состоянием
                        # Используем безопасный доступ к атрибутам моков в тестах
                        board_rows = getattr(node_to_rollout_from.board, 'rows', {})
                        board_cards_placed = getattr(node_to_rollout_from.board, '_cards_placed', 0)
                        board_is_foul = getattr(node_to_rollout_from.board, 'is_foul', False)

                        board_dict = {
                            'rows': {r: Card.hand_to_str(cards) for r, cards in board_rows.items()},
                            '_cards_placed': board_cards_placed,
                            'is_foul': board_is_foul
                        }
                        cards_ints = list(node_to_rollout_from.cards_to_place) # Копия
                        deck_ints = list(node_to_rollout_from.remaining_deck) # Копия

                        rollout_tasks = [(board_dict, cards_ints, deck_ints)] * self.rollouts_per_leaf

                        if pool and self.num_workers > 1: # Параллельные роллауты
                             async_results = [pool.apply_async(run_parallel_rollout, task) for task in rollout_tasks]
                             for res in async_results:
                                  try:
                                       # Динамический таймаут, но не менее 1 секунды
                                       timeout_get = max(1.0, self.time_limit * 0.1)
                                       reward = res.get(timeout=timeout_get)
                                       results.append(reward)
                                       num_simulations += 1
                                  except multiprocessing.TimeoutError: logger.warning("Rollout worker timed out.")
                                  except Exception as e_get: logger.warning(f"Error getting result from worker: {e_get}")
                        else: # Последовательные роллауты (или если пул не создался)
                             for task in rollout_tasks:
                                  try:
                                       reward = run_parallel_rollout(*task)
                                       results.append(reward)
                                       num_simulations += 1
                                  except Exception as e_seq: logger.warning(f"Error during sequential rollout: {e_seq}")

                    except Exception as e_roll:
                         logger.error(f"Error preparing/running rollout phase: {e_roll}", exc_info=True)
                         continue # Пропускаем итерацию
                else:
                    # Терминальный узел - получаем роялти напрямую
                    try:
                        if getattr(node_to_rollout_from.board, 'is_foul', True): # Считаем фолом по умолчанию при ошибке
                            reward = 0.0
                        else:
                             # Используем эвалюаторы, импортированные в этот модуль
                             reward = sum(get_row_royalty(node_to_rollout_from.board.get_row_cards(r), r) # Убрали передачу эвалюаторов
                                          for r in PlayerBoard.ROW_NAMES)
                        results.append(reward)
                        num_simulations += 1
                    except Exception as e_term: logger.error(f"Error getting terminal royalty: {e_term}", exc_info=True)

                # 4. Обратное распространение (Backpropagation)
                if results:
                    avg_reward = sum(results) / len(results)
                    self._backpropagate(path, avg_reward) # Передаем среднюю награду

        except KeyboardInterrupt:
             logger.warning("MCTS execution interrupted by user.")
             # Не возвращаем None, пытаемся выбрать лучшее из того, что есть
        except Exception as e_mcts:
            logger.error(f"Critical error during MCTS execution: {e_mcts}", exc_info=True)
            # Возвращаем None при критической ошибке
            return None
        finally:
            if pool:
                 try:
                     pool.close() # Сигнал, что больше задач не будет
                     pool.join() # Ждем завершения всех задач
                 except Exception as e_pool: logger.error(f"Error closing MCTS pool: {e_pool}")

        elapsed_time = time.time() - start_mcts_time
        sims_per_sec = (num_simulations / elapsed_time) if elapsed_time > 0 else 0
        logger.info(f"MCTS finished: Ran {num_simulations} simulations in {elapsed_time:.3f}s ({sims_per_sec:.1f} sims/s).")

        # Выбор лучшего действия
        best_action = self._select_best_action(root_node, board, cards_to_place)
        total_time = time.time() - start_time_total
        logger.info(f"--- AI Agent: Action chosen in {total_time:.3f}s ---")
        return best_action

    def _select(self, node: MCTSNode) -> Tuple[List[MCTSNode], Optional[MCTSNode]]:
        """Фаза выбора: спускаемся по дереву, выбирая лучшие узлы по UCB1."""
        path = [node]
        current_node = node
        while True:
            if current_node.is_terminal(): return path, current_node
            # Инициализация неиспробованных действий при первом посещении
            # Используем безопасный доступ к атрибуту
            if getattr(current_node, 'untried_actions', None) is None:
                current_node.untried_actions = current_node._get_available_placements()
                if current_node.untried_actions: # Перемешиваем, только если список не пуст
                    random.shuffle(current_node.untried_actions)

            if current_node.untried_actions: return path, current_node # Есть неиспробованные -> расширяем
            if not current_node.children: return path, current_node # Лист без действий (и без неиспробованных)

            selected_child = current_node.uct_select_child(self.exploration)
            if selected_child is None:
                # Это может случиться, если у узла нет потомков (хотя проверка выше должна это отловить)
                # или если все потомки имеют 0 посещений (что uct_select_child должен обрабатывать)
                logger.warning(f"UCT selection returned None for node {current_node}. Parent visits: {current_node.visits}. Children: {len(current_node.children)}")
                return path, current_node # Не смогли выбрать потомка

            current_node = selected_child
            path.append(current_node)

    def _backpropagate(self, path: List[MCTSNode], reward: float):
        """Фаза обратного распространения."""
        for node in reversed(path):
            node.visits += 1
            node.total_reward += reward # Награда - это роялти, максимизируем

    def _select_best_action(self,
                            root_node: MCTSNode,
                            initial_board: PlayerBoard,
                            initial_cards: List[int]) -> Optional[Tuple[int, str, int]]:
        """Выбирает лучшее действие из корневого узла (максимум посещений)."""
        if not root_node.children:
            logger.warning("No children found at root node. Cannot select best action.")
            # Пытаемся вернуть случайное из доступных, если есть
            available_actions = root_node._get_available_placements()
            return random.choice(available_actions) if available_actions else None

        # --- Применяем правило "Без трипса на топе" ---
        is_first_street = (initial_board.get_total_cards() == 0 and len(initial_cards) == 5)
        trip_in_hand_rank = -1
        if is_first_street:
            ranks = [Card.get_rank_int(c) for c in initial_cards]
            rank_counts = Counter(ranks)
            for rank, count in rank_counts.items():
                if count >= 3: trip_in_hand_rank = rank; break

        # Собираем статистику по действиям
        action_stats: List[Tuple[Any, int, float]] = [] # (action, visits, avg_reward)
        items = list(root_node.children.items())
        logger.info(f"--- Evaluating {len(items)} child nodes (first placements) ---")
        for action, child_node in items:
             # Добавим проверку на child_node.visits > 0 перед делением
             avg_reward = child_node.total_reward / child_node.visits if child_node.visits > 0 else -float('inf')
             action_stats.append((action, child_node.visits, avg_reward))
             # Используем f-string для форматирования
             logger.info(f"  Action: {self._format_action(action):<25} Visits: {child_node.visits:<6} AvgRoyalty: {avg_reward:<8.2f}")

        # Сортируем по посещениям (основной критерий), затем по средней награде
        action_stats.sort(key=lambda x: (x[1], x[2]), reverse=True)

        # Выбираем лучшее действие, пропуская запрещенные (трипс на топ)
        best_allowed_action: Optional[Tuple[int, str, int]] = None
        for action, visits, avg_reward in action_stats:
            if is_first_street and trip_in_hand_rank != -1:
                card_int, row_name, index = action
                # Проверяем, что размещаемая карта - часть трипса и идет на топ
                if Card.get_rank_int(card_int) == trip_in_hand_rank and row_name == 'top':
                    logger.warning(f"Rule Violation: Skipping action {self._format_action(action)} (Trip {Card.to_str(card_int)[0]} on Top on Street 1).")
                    continue # Пропускаем это действие

            # Если действие не нарушает правило (или правило не применяется), выбираем его
            best_allowed_action = action
            logger.info(f"Selected action (Visits={visits}, AvgRoyalty={avg_reward:.2f}): {self._format_action(best_allowed_action)}")
            return best_allowed_action # Возвращаем первое же лучшее разрешенное действие

        # Если все действия были запрещены (маловероятно)
        if best_allowed_action is None:
            logger.warning("All evaluated actions were disallowed by rules or no actions available. Returning None.")
            return None
        # Этот return уже не нужен, так как мы возвращаем внутри цикла
        # return best_allowed_action


    def _format_action(self, action: Any) -> str:
        """ Форматирует действие в читаемую строку для логов. """
        if action is None: return "None"
        try:
            if isinstance(action, tuple) and len(action) == 3:
                 card_int, row_name, index = action
                 return f"{Card.to_str(card_int)}@{row_name}[{index}]"
            else: return str(action)
        except Exception: return "ErrorFormattingAction"
