# mcts_agent.py v2.0 (Refactored for Set Placement)
"""
Реализация MCTS-агента для задачи размещения НАБОРА карт OFC Pineapple.
Использует переработанный MCTSNode.
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
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP # Добавлен RANK_MAP
    # Импортируем MCTSNode и воркер
    from mcts_node import MCTSNode, run_parallel_rollout
    # Импортируем эвалюаторы и функции скоринга из ofc_evaluators
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS # Для правила Фантазии
    )
    # Импортируем 5-card instance напрямую
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import modules in mcts_agent.py: {e}")
    # Заглушки ... (остаются как были)
    class PlayerBoard: pass
    class Card: pass
    class Deck: FULL_DECK_CARDS = set()
    class MCTSNode: pass
    def run_parallel_rollout(*args): return 0.0
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    WORST_RANK = 9999; WORST_CLASS = 9
    def check_board_foul(*args): return False
    def get_row_royalty(*args): return 0
    ROYALTY_TOP_PAIRS = {}
    RANK_MAP = {}
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    raise ImportError("Missing core logic/node/evaluator modules for MCTSAgent") from e

# Получаем логгер (без изменений)
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)
    # ... (настройка хендлера)

class MCTSAgent:
    """ Агент MCTS для размещения НАБОРА карт OFC Pineapple. """
    DEFAULT_EXPLORATION: float = 1.414
    DEFAULT_TIME_LIMIT_MS: int = 5000
    DEFAULT_NUM_WORKERS: int = max(1, multiprocessing.cpu_count() - 1 if multiprocessing.cpu_count() > 1 else 1)
    DEFAULT_ROLLOUTS_PER_LEAF: int = 4 # Оставляем 4 по умолчанию

    def __init__(self,
                 exploration: Optional[float] = None,
                 time_limit_ms: Optional[int] = None,
                 num_workers: Optional[int] = None,
                 rollouts_per_leaf: Optional[int] = None):
        """ Инициализирует MCTS-агента. """
        self.exploration: float = exploration if exploration is not None else self.DEFAULT_EXPLORATION
        time_limit_val: int = time_limit_ms if time_limit_ms is not None else self.DEFAULT_TIME_LIMIT_MS
        self.time_limit: float = max(0.1, time_limit_val / 1000.0)
        max_cpus = multiprocessing.cpu_count()
        requested_workers: int = num_workers if num_workers is not None else self.DEFAULT_NUM_WORKERS
        self.num_workers: int = max(1, min(requested_workers, max_cpus, 8))
        self.rollouts_per_leaf: int = rollouts_per_leaf if rollouts_per_leaf is not None else self.DEFAULT_ROLLOUTS_PER_LEAF

        # Убрана корректировка rollouts_per_leaf для num_workers=1,
        # так как параллелизация теперь только для роллаутов, а не для выбора.
        # Если rollouts_per_leaf > 1, они будут выполняться последовательно при num_workers=1.

        logger.info(f"MCTS Agent initialized: TimeLimit={self.time_limit:.2f}s, Exploration={self.exploration}, "
                    f"Workers={self.num_workers}, RolloutsPerLeaf={self.rollouts_per_leaf}")
        # ... (настройка multiprocessing start method остается)
        try:
            current_method = multiprocessing.get_start_method(allow_none=True)
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


    def choose_placement(self,
                         initial_board: PlayerBoard,
                         cards_just_dealt: List[int],
                         current_remaining_deck: Set[int]) -> Optional[Dict[str, Any]]:
        """
        Выбирает лучшее РАЗМЕЩЕНИЕ для НАБОРА карт с помощью MCTS.

        Args:
            initial_board (PlayerBoard): Текущее состояние доски *до* размещения новых карт.
            cards_just_dealt (List[int]): Карты, которые только что были разданы (5 или 3).
            current_remaining_deck (Set[int]): Карты, оставшиеся в колоде *после* раздачи cards_just_dealt.

        Returns:
            Optional[Dict[str, Any]]: Словарь с информацией о лучшем размещении
                                      {'placements': [(card, row, idx), ...], 'discarded': card_or_none}
                                      или None, если ход невозможен или произошла ошибка.
        """
        start_time_total = time.time()
        if not cards_just_dealt:
            logger.warning("MCTSAgent: choose_placement called with no cards dealt.")
            return None
        if initial_board.is_complete():
            logger.warning("MCTSAgent: choose_placement called with complete board.")
            return None

        num_dealt = len(cards_just_dealt)
        street = 1 if initial_board.get_total_cards() == 0 else (initial_board.get_total_cards() // 2) + 1 # Приблизительно
        logger.info(f"\n--- AI Agent: Choosing placement for {num_dealt} cards (Street ~{street}) ---")
        logger.info(f"Initial Board state:\n{initial_board}")
        logger.info(f"Cards Dealt: {[Card.to_str(c) for c in cards_just_dealt]}")
        # logger.debug(f"Remaining deck size: {len(current_remaining_deck)}")

        # Создаем корневой узел MCTS
        # Корень представляет состояние *до* размещения текущих карт
        try:
            root_node = MCTSNode(
                board=initial_board,
                remaining_deck=current_remaining_deck, # Колода для симуляций из *дочерних* узлов
                parent=None,
                placement_info=None
            )
            # Генерируем возможные первые ходы (размещения текущего набора)
            # Это нужно сделать один раз для корня, чтобы создать дочерние узлы
            root_node.untried_next_states = root_node._generate_next_states(cards_just_dealt)
            if not root_node.untried_next_states:
                 logger.error("Failed to generate any initial placements from root node.")
                 return None
            logger.info(f"Generated {len(root_node.untried_next_states)} possible initial placements.")

        except Exception as e_root:
            logger.error(f"Failed to create or initialize MCTS root node: {e_root}", exc_info=True)
            return None

        start_mcts_time = time.time()
        num_simulations = 0
        pool = None

        try:
            # Создаем пул процессов для роллаутов
            if self.num_workers > 1:
                 try:
                     pool = multiprocessing.Pool(processes=self.num_workers)
                     logger.debug(f"Created multiprocessing pool with {self.num_workers} workers.")
                 except Exception as e_pool_create:
                     logger.error(f"Failed to create multiprocessing pool: {e_pool_create}. Falling back to 1 worker.", exc_info=True)
                     self.num_workers = 1

            # Основной цикл MCTS
            while time.time() - start_mcts_time < self.time_limit:
                # 1. Выбор (Selection)
                path, leaf_node = self._select(root_node)
                if leaf_node is None:
                     logger.warning("Selection phase returned None leaf node. Breaking MCTS loop.")
                     break

                # 2. Расширение (Expansion) - если узел не терминальный и есть нераскрытые состояния
                node_to_rollout_from = leaf_node
                if not leaf_node.is_terminal():
                    # --- Логика генерации следующих состояний ПЕРЕД расширением ---
                    if leaf_node.untried_next_states is None:
                        # Определяем, сколько карт раздать для следующей улицы
                        cards_on_board = leaf_node.board.get_total_cards()
                        num_cards_to_deal_next = 3 # По умолчанию для улиц 2-5
                        # Проверяем, не последняя ли это улица (11 карт -> раздаем 3, ставим 2)
                        if cards_on_board >= 11: # Если 11 или 12 карт, следующей улицы нет
                             num_cards_to_deal_next = 0
                        elif cards_on_board == 0: # Если это корень (0 карт), то раздаем 5 (уже сделано)
                             # Этот случай не должен происходить здесь, т.к. корень инициализируется отдельно
                             logger.error("Selection reached root node without untried states, should not happen.")
                             num_cards_to_deal_next = 0
                        # elif cards_on_board == 5: num_cards_to_deal_next = 3
                        # elif cards_on_board == 7: num_cards_to_deal_next = 3
                        # elif cards_on_board == 9: num_cards_to_deal_next = 3

                        if num_cards_to_deal_next > 0:
                            # Симулируем раздачу карт для следующей улицы
                            if len(leaf_node.remaining_deck) < num_cards_to_deal_next:
                                logger.warning(f"Not enough cards in deck ({len(leaf_node.remaining_deck)}) to simulate next street deal ({num_cards_to_deal_next}) for node expansion.")
                                # Узел становится псевдо-терминальным для симуляции
                            else:
                                try:
                                    # Используем random.sample для получения случайных карт без извлечения
                                    simulated_deal = random.sample(list(leaf_node.remaining_deck), num_cards_to_deal_next)
                                    # Генерируем возможные состояния
                                    leaf_node.untried_next_states = leaf_node._generate_next_states(simulated_deal)
                                    # logger.debug(f"Generated {len(leaf_node.untried_next_states)} next states for node {leaf_node}")
                                except ValueError as ve:
                                     logger.error(f"ValueError during simulated deal sampling: {ve}")
                                     leaf_node.untried_next_states = [] # Ошибка генерации
                                except Exception as e_gen:
                                     logger.error(f"Error generating next states in selection: {e_gen}", exc_info=True)
                                     leaf_node.untried_next_states = [] # Ошибка генерации
                        else:
                             leaf_node.untried_next_states = [] # Нет следующей улицы

                    # --- Само расширение ---
                    if leaf_node.untried_next_states: # Если есть что расширять
                        expanded_node = leaf_node.expand()
                        if expanded_node:
                            node_to_rollout_from = expanded_node
                            path.append(expanded_node)
                        # Если expand вернул None (ошибка или нет действий), node_to_rollout_from остается leaf_node
                    # else: logger.debug(f"Node {leaf_node} has no untried states to expand.")

                # 3. Симуляция (Rollout)
                results: List[float] = []
                # Роллаут запускается из node_to_rollout_from (либо лист, либо новый узел)
                try:
                    # Подготовка данных для воркеров
                    # Передаем копии данных, чтобы избежать проблем с состоянием
                    board_to_sim = node_to_rollout_from.board # Доска уже в нужном состоянии
                    deck_to_sim = list(node_to_rollout_from.remaining_deck) # Оставшаяся колода

                    # Преобразуем доску в словарь для передачи
                    board_dict = {
                        'rows': {r: Card.hand_to_str(cards) for r, cards in board_to_sim.rows.items()},
                        '_cards_placed': board_to_sim.get_total_cards(),
                        # 'is_foul' не передаем, он определяется в конце роллаута
                    }

                    rollout_tasks = [(board_dict, deck_to_sim)] * self.rollouts_per_leaf

                    if pool and self.num_workers > 1: # Параллельные роллауты
                         async_results = [pool.apply_async(run_parallel_rollout, task) for task in rollout_tasks]
                         for res in async_results:
                              try:
                                   timeout_get = max(1.0, self.time_limit * 0.1)
                                   reward = res.get(timeout=timeout_get)
                                   results.append(reward)
                                   num_simulations += 1
                              except multiprocessing.TimeoutError: logger.warning("Rollout worker timed out.")
                              except Exception as e_get: logger.warning(f"Error getting result from worker: {e_get}")
                    else: # Последовательные роллауты
                         for task in rollout_tasks:
                              try:
                                   reward = run_parallel_rollout(*task)
                                   results.append(reward)
                                   num_simulations += 1
                              except Exception as e_seq: logger.warning(f"Error during sequential rollout: {e_seq}")

                except Exception as e_roll:
                     logger.error(f"Error preparing/running rollout phase: {e_roll}", exc_info=True)
                     # Не пропускаем итерацию, пытаемся сделать backpropagate с тем, что есть (может быть [])

                # 4. Обратное распространение (Backpropagation)
                if results:
                    avg_reward = sum(results) / len(results)
                    self._backpropagate(path, avg_reward) # Передаем среднюю награду
                # else: logger.debug("No results from rollout phase.") # Если роллауты не удались

        except KeyboardInterrupt:
             logger.warning("MCTS execution interrupted by user.")
        except Exception as e_mcts:
            logger.error(f"Critical error during MCTS execution: {e_mcts}", exc_info=True)
            return None # Возвращаем None при критической ошибке
        finally:
            if pool:
                 try: pool.close(); pool.join()
                 except Exception as e_pool: logger.error(f"Error closing MCTS pool: {e_pool}")

        elapsed_time = time.time() - start_mcts_time
        sims_per_sec = (num_simulations / elapsed_time) if elapsed_time > 0 else 0
        logger.info(f"MCTS finished: Ran {num_simulations} simulations in {elapsed_time:.3f}s ({sims_per_sec:.1f} sims/s).")

        # Выбор лучшего действия (размещения)
        best_placement_info = self._select_best_placement(root_node, cards_just_dealt)
        total_time = time.time() - start_time_total
        logger.info(f"--- AI Agent: Placement chosen in {total_time:.3f}s ---")
        return best_placement_info

    def _select(self, node: MCTSNode) -> Tuple[List[MCTSNode], Optional[MCTSNode]]:
        """Фаза выбора: спускаемся по дереву, выбирая лучшие узлы по UCB1."""
        path = [node]
        current_node = node
        while True:
            if current_node.is_terminal():
                # logger.debug("Selection reached terminal node.")
                return path, current_node

            # Если узел еще не генерировал следующие состояния, останавливаемся здесь
            if current_node.untried_next_states is None:
                # logger.debug(f"Selection stopped at node {current_node} for state generation.")
                return path, current_node

            # Если есть нераскрытые состояния (новые дочерние узлы еще не созданы), останавливаемся
            if current_node.untried_next_states:
                # logger.debug(f"Selection stopped at node {current_node} for expansion.")
                return path, current_node

            # Если нет нераскрытых состояний и нет дочерних узлов (тупик?), останавливаемся
            if not current_node.children:
                logger.warning(f"Selection reached non-terminal node {current_node} with no children and no untried states.")
                return path, current_node

            # Выбираем лучшего потомка по UCB1
            selected_child = current_node.uct_select_child(self.exploration)
            if selected_child is None:
                logger.warning(f"UCT selection returned None for node {current_node}. Parent visits: {current_node.visits}. Children: {len(current_node.children)}")
                return path, current_node # Не смогли выбрать потомка

            current_node = selected_child
            path.append(current_node)


    def _backpropagate(self, path: List[MCTSNode], reward: float):
        """Фаза обратного распространения."""
        for node in reversed(path):
            node.visits += 1
            node.total_reward += reward

    def _select_best_placement(self,
                               root_node: MCTSNode,
                               initial_cards_dealt: List[int]) -> Optional[Dict[str, Any]]:
        """
        Выбирает лучшее размещение из дочерних узлов корневого узла.
        Применяет правило "Без трипса на топе на улице 1".
        """
        if not root_node.children:
            logger.warning("No children found at root node. Cannot select best placement.")
            # Пытаемся вернуть первое из сгенерированных, если есть
            if hasattr(root_node, '_generated_states_for_expand') and root_node._generated_states_for_expand:
                 _, _, placement_info = next(iter(root_node._generated_states_for_expand.values()))
                 logger.warning("Returning first generated placement as fallback.")
                 return placement_info
            return None

        # --- Проверка правила "Без трипса на топе" ---
        is_first_street = (root_node.board.get_total_cards() == 0 and len(initial_cards_dealt) == 5)
        trip_in_hand_rank = -1
        if is_first_street:
            ranks = [Card.get_rank_int(c) for c in initial_cards_dealt]
            rank_counts = Counter(ranks)
            for rank, count in rank_counts.items():
                if count >= 3: trip_in_hand_rank = rank; break
            if trip_in_hand_rank != -1:
                 logger.info(f"Rule Check: First street with trip of rank {trip_in_hand_rank} detected.")

        # Собираем статистику по дочерним узлам (первым ходам)
        child_stats: List[Tuple[Dict[str, Any], int, float]] = [] # (placement_info, visits, avg_reward)
        items = list(root_node.children.items())
        logger.info(f"--- Evaluating {len(items)} child nodes (initial placements) ---")
        for placement_key, child_node in items:
             avg_reward = child_node.total_reward / child_node.visits if child_node.visits > 0 else -float('inf')
             # Получаем placement_info из узла (если оно там хранится) или из ключа?
             # Лучше хранить в узле. Добавим проверку.
             p_info = getattr(child_node, 'placement_info', None)
             if p_info is None:
                  logger.warning(f"Child node {child_node} missing placement_info.")
                  continue # Пропускаем узел без информации о размещении

             child_stats.append((p_info, child_node.visits, avg_reward))
             # Форматируем для лога
             log_placements = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_info.get('placements', [])])
             log_discard = f"(Discard: {Card.to_str(p_info.get('discarded'))})" if p_info.get('discarded') else ""
             logger.info(f"  Placement: {log_placements} {log_discard} -> Visits: {child_node.visits:<6} AvgReward: {avg_reward:<8.2f}")

        # Сортируем по посещениям (основной критерий), затем по средней награде
        child_stats.sort(key=lambda x: (x[1], x[2]), reverse=True)

        # Выбираем лучшее действие, пропуская запрещенные
        best_allowed_placement: Optional[Dict[str, Any]] = None
        for placement_info, visits, avg_reward in child_stats:
            # Применяем правило, если нужно
            skip_placement = False
            if is_first_street and trip_in_hand_rank != -1:
                placements = placement_info.get('placements', [])
                for card_int, row_name, index in placements:
                    if Card.get_rank_int(card_int) == trip_in_hand_rank and row_name == 'top':
                        log_placements = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in placements])
                        logger.warning(f"Rule Violation: Skipping placement {log_placements} (Trip rank {trip_in_hand_rank} on Top on Street 1).")
                        skip_placement = True
                        break # Достаточно одного нарушения в размещении

            if not skip_placement:
                best_allowed_placement = placement_info
                log_placements = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in best_allowed_placement.get('placements', [])])
                log_discard = f"(Discard: {Card.to_str(best_allowed_placement.get('discarded'))})" if best_allowed_placement.get('discarded') else ""
                logger.info(f"Selected placement (Visits={visits}, AvgReward={avg_reward:.2f}): {log_placements} {log_discard}")
                return best_allowed_placement # Возвращаем первое же лучшее разрешенное

        # Если все действия были запрещены (маловероятно)
        if best_allowed_placement is None:
            logger.warning("All evaluated placements were disallowed by rules or no placements available. Returning None.")
            # Можно попробовать вернуть первое попавшееся, если они были
            if child_stats:
                 logger.warning("Returning the first placement despite potential rule violation as fallback.")
                 return child_stats[0][0]
            return None
        # Этот return уже не нужен
        # return best_allowed_placement

    # Убран _format_action, так как форматирование теперь в _select_best_placement
