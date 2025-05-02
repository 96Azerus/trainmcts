# mcts_agent.py v2.5 (Exception Handling Fix)
"""
Реализация MCTS-агента для задачи размещения НАБОРА карт OFC Pineapple.
Использует RAVE и случайные симуляции с трекингом действий из MCTSNode.
Исправлен SyntaxError в блоке обработки исключений MCTS.
"""

import time
import random
import multiprocessing
import traceback
import sys
import logging
import math
from typing import Optional, Any, List, Tuple, Set, Dict
from collections import Counter

# Импорты из локальных модулей
try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP
    from mcts_node import MCTSNode, run_parallel_rollout, RAVE_K
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import modules in mcts_agent.py: {e}")
    # Заглушки ...
    class PlayerBoard: pass
    class Card: pass
    class Deck: FULL_DECK_CARDS = set()
    class MCTSNode: pass
    def run_parallel_rollout(*args): return 0.0, []
    RAVE_K = 500.0
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid")
    WORST_RANK = 9999; WORST_CLASS = 9
    def check_board_foul(*args): return False
    def get_row_royalty(*args): return 0
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {}
    class MockEvaluator5Card: evaluate = lambda s, c: 9999
    evaluator_5card = MockEvaluator5Card()
    raise ImportError("Missing core logic/node/evaluator modules for MCTSAgent") from e

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class MCTSAgent:
    """ Агент MCTS для размещения НАБОРА карт OFC Pineapple с RAVE (случайные роллауты). """
    DEFAULT_EXPLORATION: float = 1.414
    DEFAULT_TIME_LIMIT_MS: int = 5000
    DEFAULT_NUM_WORKERS: int = max(1, multiprocessing.cpu_count() - 1 if multiprocessing.cpu_count() > 1 else 1)
    DEFAULT_ROLLOUTS_PER_LEAF: int = 10

    def __init__(self,
                 exploration: Optional[float] = None,
                 time_limit_ms: Optional[int] = None,
                 num_workers: Optional[int] = None,
                 rollouts_per_leaf: Optional[int] = None):
        self.exploration: float = exploration if exploration is not None else self.DEFAULT_EXPLORATION
        time_limit_val: int = time_limit_ms if time_limit_ms is not None else self.DEFAULT_TIME_LIMIT_MS
        self.time_limit: float = max(0.1, time_limit_val / 1000.0)
        max_cpus = multiprocessing.cpu_count()
        requested_workers: int = num_workers if num_workers is not None else self.DEFAULT_NUM_WORKERS
        self.num_workers: int = max(1, min(requested_workers, max_cpus, 8))
        self.rollouts_per_leaf: int = rollouts_per_leaf if rollouts_per_leaf is not None else self.DEFAULT_ROLLOUTS_PER_LEAF

        logger.info(f"MCTS Agent initialized: TimeLimit={self.time_limit:.2f}s, Exploration={self.exploration}, "
                    f"Workers={self.num_workers}, RolloutsPerLeaf={self.rollouts_per_leaf}, RAVE_K={RAVE_K}")
        try:
            current_method = multiprocessing.get_start_method(allow_none=True)
            if current_method != 'spawn':
                available_methods = multiprocessing.get_all_start_methods()
                if 'spawn' in available_methods:
                    try: multiprocessing.set_start_method('spawn', force=True); logger.info("Set MP start method to 'spawn'.")
                    except Exception as e_set: logger.warning(f"Could not set MP start method to 'spawn': {e_set}.")
                else: logger.warning("'spawn' MP start method not available.")
        except Exception as e: logger.warning(f"Error checking/setting MP start method: {e}.")


    def choose_placement(self,
                         initial_board: PlayerBoard,
                         cards_just_dealt: List[int],
                         current_remaining_deck: Set[int]) -> Optional[Dict[str, Any]]:
        """
        Выбирает лучшее РАЗМЕЩЕНИЕ для НАБОРА карт с помощью MCTS + RAVE (случайные роллауты).
        """
        start_time_total = time.time()
        if not cards_just_dealt: logger.warning("MCTSAgent: choose_placement called with no cards dealt."); return None
        if initial_board.is_complete(): logger.warning("MCTSAgent: choose_placement called with complete board."); return None

        num_dealt = len(cards_just_dealt)
        cards_on_board_start = initial_board.get_total_cards()
        street = 1 if cards_on_board_start == 0 else (cards_on_board_start // 2) + 2
        logger.info(f"\n--- AI Agent: Choosing placement for {num_dealt} cards (Street ~{street}) ---")
        logger.info(f"Initial Board state:\n{initial_board}")
        logger.info(f"Cards Dealt: {[Card.to_str(c) for c in cards_just_dealt]}")
        logger.info(f"Remaining deck size: {len(current_remaining_deck)}")

        try:
            root_node = MCTSNode(board=initial_board, remaining_deck=current_remaining_deck, parent=None, placement_info=None)
        except Exception as e_root: logger.error(f"Failed to create MCTS root node: {e_root}", exc_info=True); return None

        start_mcts_time = time.time(); num_simulations = 0; pool = None
        try:
            if self.num_workers > 1:
                 try: pool = multiprocessing.Pool(processes=self.num_workers); logger.debug(f"Created MP pool with {self.num_workers} workers.")
                 except Exception as e_pool_create: logger.error(f"Failed to create MP pool: {e_pool_create}. Falling back to 1 worker.", exc_info=True); self.num_workers = 1

            # Основной цикл MCTS
            while time.time() - start_mcts_time < self.time_limit:
                # 1. Выбор (Selection)
                path, leaf_node = self._select(root_node)
                if leaf_node is None: logger.warning("Selection phase returned None leaf node."); break

                # 2. Расширение (Expansion)
                node_to_rollout_from = leaf_node
                if not leaf_node.is_terminal():
                    if leaf_node.untried_next_states is None:
                        cards_on_board = leaf_node.board.get_total_cards()
                        cards_to_generate_for: List[int]
                        if leaf_node is root_node: cards_to_generate_for = cards_just_dealt
                        else:
                            num_cards_to_deal_next = 3 if cards_on_board < 11 else 0
                            if num_cards_to_deal_next > 0 and len(leaf_node.remaining_deck) >= num_cards_to_deal_next:
                                try: cards_to_generate_for = random.sample(list(leaf_node.remaining_deck), num_cards_to_deal_next)
                                except Exception: cards_to_generate_for = []
                            else: cards_to_generate_for = []
                        leaf_node.untried_next_states = leaf_node._generate_next_states(cards_to_generate_for) if cards_to_generate_for else []

                    if leaf_node.untried_next_states:
                        expanded_node = leaf_node.expand() # expand теперь учитывает PW
                        if expanded_node: node_to_rollout_from = expanded_node; path.append(expanded_node)

                # 3. Симуляция (Rollout)
                rollout_results: List[Tuple[float, List[Dict[str, Any]]]] = []
                try:
                    board_to_sim = node_to_rollout_from.board; deck_to_sim = list(node_to_rollout_from.remaining_deck)
                    board_dict = {'rows': {r: Card.hand_to_str(cards) for r, cards in board_to_sim.rows.items()}, '_cards_placed': board_to_sim.get_total_cards()}
                    rollout_tasks = [(board_dict, deck_to_sim)] * self.rollouts_per_leaf

                    if pool and self.num_workers > 1:
                         async_results = [pool.apply_async(run_parallel_rollout, task) for task in rollout_tasks]
                         for res in async_results:
                              try:
                                   timeout_get = max(0.5, self.time_limit * 0.1)
                                   reward, actions = res.get(timeout=timeout_get)
                                   rollout_results.append((reward, actions)); num_simulations += 1
                              except multiprocessing.TimeoutError: logger.warning("Rollout worker timed out.")
                              except Exception as e_get: logger.warning(f"Error getting result from worker: {e_get}")
                    else:
                         for task in rollout_tasks:
                              try:
                                   reward, actions = run_parallel_rollout(*task)
                                   rollout_results.append((reward, actions)); num_simulations += 1
                              except Exception as e_seq: logger.warning(f"Error during sequential rollout: {e_seq}")
                except Exception as e_roll: logger.error(f"Error preparing/running rollout phase: {e_roll}", exc_info=True)

                # 4. Обратное распространение (Backpropagation)
                if rollout_results:
                    for reward, actions in rollout_results:
                         self._backpropagate_standard(path, reward)
                         self._backpropagate_rave(path, actions, reward)

        except KeyboardInterrupt:
             logger.warning("MCTS execution interrupted by user.")
             # --- ИСПРАВЛЕНО: Корректная обработка pool при KeyboardInterrupt ---
             if pool:
                 try:
                     logger.warning("Terminating pool due to KeyboardInterrupt...")
                     pool.terminate()
                     pool.join()
                     logger.warning("Pool terminated.")
                 except Exception as e_pool_term:
                     logger.error(f"Error terminating pool after KeyboardInterrupt: {e_pool_term}")
             return None # Возвращаем None при прерывании
        except Exception as e_mcts:
            logger.error(f"Critical error during MCTS execution: {e_mcts}", exc_info=True)
            # --- ИСПРАВЛЕНО: Корректная обработка pool при Exception ---
            if pool:
                try:
                    logger.warning("Terminating pool due to MCTS error...")
                    pool.terminate()
                    pool.join()
                    logger.warning("Pool terminated.")
                except Exception as e_pool_term:
                    logger.error(f"Error terminating pool after MCTS error: {e_pool_term}")
            # --- Конец исправления ---
            return None
        finally:
            # Этот блок finally закроет пул, если он не был завершен принудительно
            if pool:
                 try:
                     pool.close()
                     pool.join()
                 except Exception as e_pool_close:
                     # Логгируем, но не падаем, если закрытие не удалось
                     logger.error(f"Error closing MCTS pool in finally block: {e_pool_close}")

        elapsed_time = time.time() - start_mcts_time
        sims_per_sec = (num_simulations / elapsed_time) if elapsed_time > 0 else 0
        logger.info(f"MCTS finished: Ran {num_simulations} simulations in {elapsed_time:.3f}s ({sims_per_sec:.1f} sims/s). Root visits: {root_node.visits}")

        best_placement_info = self._select_best_placement(root_node, cards_just_dealt)
        total_time = time.time() - start_time_total
        logger.info(f"--- AI Agent: Placement chosen in {total_time:.3f}s ---")
        return best_placement_info

    def _select(self, node: MCTSNode) -> Tuple[List[MCTSNode], Optional[MCTSNode]]:
        """Фаза выбора: спускаемся по дереву, выбирая лучшие узлы по UCB1 + RAVE."""
        # ... (код функции _select без изменений) ...
        path = [node]; current_node = node
        while True:
            if current_node.is_terminal(): return path, current_node
            if current_node.untried_next_states is None:
                if current_node is not node:
                    cards_on_board = current_node.board.get_total_cards()
                    num_cards_to_deal_next = 3 if cards_on_board < 11 else 0
                    cards_to_generate_for = []
                    if num_cards_to_deal_next > 0 and len(current_node.remaining_deck) >= num_cards_to_deal_next:
                        try: cards_to_generate_for = random.sample(list(current_node.remaining_deck), num_cards_to_deal_next)
                        except Exception: pass
                    current_node.untried_next_states = current_node._generate_next_states(cards_to_generate_for) if cards_to_generate_for else []
                return path, current_node
            if current_node.untried_next_states:
                # --- ИЗМЕНЕНИЕ: Проверяем PW перед возвратом для expand ---
                # Если есть нераскрытые состояния, но PW не позволяет расширять,
                # то мы должны выбрать существующего ребенка, а не возвращать узел для expand.
                num_children = len(current_node.children)
                allowed_children = PW_C * math.pow(current_node.visits + 1, PW_ALPHA)
                if num_children < allowed_children:
                    return path, current_node # PW позволяет, возвращаем для expand
                # Если PW не позволяет, продолжаем выбор существующего ребенка ниже
                else:
                     logger.debug(f"PW limit hit during selection for node {current_node}, selecting existing child.")
                     pass # Переходим к выбору существующего ребенка

            if not current_node.children:
                # Эта ситуация теперь менее вероятна из-за PW, но возможна, если все untried были плохими
                logger.warning(f"Selection reached non-terminal node {current_node} with no children and no untried states allowed by PW.")
                return path, current_node

            selected_child = current_node.uct_select_child(self.exploration)
            if selected_child is None:
                logger.warning(f"UCT selection returned None for node {current_node}.")
                if current_node.children: selected_child = random.choice(list(current_node.children.values()))
                if selected_child is None: return path, current_node
            current_node = selected_child; path.append(current_node)


    def _backpropagate_standard(self, path: List[MCTSNode], reward: float):
        """Стандартное обратное распространение."""
        # ... (код функции _backpropagate_standard без изменений) ...
        if path: path[-1].backpropagate(reward)


    def _backpropagate_rave(self, path: List[MCTSNode], simulation_actions: List[Dict[str, Any]], reward: float):
        """Обратное распространение для RAVE (AMAF)."""
        # ... (код функции _backpropagate_rave без изменений) ...
        sim_action_keys = set()
        for action in simulation_actions:
            placements = action.get('placements')
            if placements:
                 try:
                     action_key = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placements]))
                     sim_action_keys.add(action_key)
                 except Exception as e: logger.warning(f"RAVE Backprop Key Error: {e}")
        if not sim_action_keys: return
        for node in path:
            for child_key, child_node in node.children.items():
                if child_key in sim_action_keys:
                    child_node.rave_visits += 1; child_node.rave_reward += reward


    def _select_best_placement(self,
                               root_node: MCTSNode,
                               initial_cards_dealt: List[int]) -> Optional[Dict[str, Any]]:
        """
        Выбирает лучшее размещение из дочерних узлов корневого узла.
        """
        # ... (код функции _select_best_placement без изменений) ...
        if not root_node.children:
            logger.warning("No children found at root node. Cannot select best placement.")
            if hasattr(ro
