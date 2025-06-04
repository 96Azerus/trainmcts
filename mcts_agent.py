# mcts_agent.py v2.5.2 (SyntaxError Fix, Logging for Best Placement Selection)
# ИСПРАВЛЕНО: SyntaxError при обработке multiprocessing.Pool.
# ИСПРАВЛЕНО: Логика num_cards_to_deal_next в _select (Проблема №1)
# ИСПРАВЛЕНО: Критерий выбора лучшего хода в _select_best_placement (Проблема №2)
# ИЗМЕНЕНО: Добавлено логирование топ-N вариантов в _select_best_placement.
"""
Реализация MCTS-агента для задачи размещения НАБОРА карт OFC Pineapple.
Использует RAVE и случайные симуляции с трекингом действий из MCTSNode.
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

try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP
    from mcts_node import MCTSNode, run_parallel_rollout, RAVE_K, PW_C, PW_ALPHA
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import modules in mcts_agent.py: {e}")
    class PlayerBoard: pass # type: ignore
    class Card: pass # type: ignore
    class Deck: FULL_DECK_CARDS = set() # type: ignore
    class MCTSNode: pass # type: ignore
    def run_parallel_rollout(*args): return 0.0, [] # type: ignore
    RAVE_K = 500.0 # type: ignore
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid") # type: ignore
    WORST_RANK = 9999; WORST_CLASS = 9 # type: ignore
    def check_board_foul(*args): return False # type: ignore
    def get_row_royalty(*args): return 0 # type: ignore
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {} # type: ignore
    class MockEvaluator5Card: evaluate = lambda s, c: 9999 # type: ignore
    evaluator_5card = MockEvaluator5Card() # type: ignore
    raise ImportError("Missing core logic/node/evaluator modules for MCTSAgent") from e

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.WARNING) # Уровень по умолчанию WARNING
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class MCTSAgent:
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
        self.num_workers: int = max(1, min(requested_workers, max_cpus, 8)) # Ограничим 8 воркерами максимум
        self.rollouts_per_leaf: int = rollouts_per_leaf if rollouts_per_leaf is not None else self.DEFAULT_ROLLOUTS_PER_LEAF

        self.transposition_table = {}
        self.cache_hits = 0
        self.cache_misses = 0

        logger.info(f"MCTS Agent initialized: TimeLimit={self.time_limit:.2f}s, Exploration={self.exploration}, "
                    f"Workers={self.num_workers}, RolloutsPerLeaf={self.rollouts_per_leaf}, RAVE_K={RAVE_K}")
        try:
            current_method = multiprocessing.get_start_method(allow_none=True)
            if current_method != 'spawn':
                available_methods = multiprocessing.get_all_start_methods()
                if 'spawn' in available_methods:
                    try: multiprocessing.set_start_method('spawn', force=True); logger.info("Set MP start method to 'spawn'.")
                    except RuntimeError: logger.warning("MP start method already set, cannot change to 'spawn'.") # RuntimeError if already set
                    except Exception as e_set: logger.warning(f"Could not set MP start method to 'spawn': {e_set}.")
                else: logger.warning("'spawn' MP start method not available.")
        except Exception as e: logger.warning(f"Error checking/setting MP start method: {e}.")


    def choose_placement(self,
                         initial_board: PlayerBoard,
                         cards_just_dealt: List[int],
                         current_remaining_deck: Set[int],
                         num_unknown_removed_cards: int = 0) -> Optional[Dict[str, Any]]:
        start_time_total = time.time()
        self.cache_hits = 0
        self.cache_misses = 0
        if not cards_just_dealt: logger.warning("MCTSAgent: choose_placement called with no cards dealt."); return None
        if initial_board.is_complete(): logger.warning("MCTSAgent: choose_placement called with complete board."); return None
        num_dealt = len(cards_just_dealt)
        cards_on_board_start = initial_board.get_total_cards()
        street = 1 if cards_on_board_start == 0 else (cards_on_board_start // 2) + 2
        logger.info(f"\n--- AI Agent: Choosing placement for {num_dealt} cards (Street ~{street}) ---")
        logger.info(f"Initial Board state:\n{initial_board}")
        logger.info(f"Cards Dealt: {[Card.to_str(c) for c in cards_just_dealt]}")
        logger.info(f"Remaining deck size (for AI): {len(current_remaining_deck)}")
        logger.info(f"Number of unknown cards removed (permanently discarded): {num_unknown_removed_cards}")
        try:
            root_node = MCTSNode(
                board=initial_board,
                remaining_deck=current_remaining_deck, # This deck still notionally includes unknown_removed
                parent=None,
                placement_info=None,
                num_unknown_removed_cards=num_unknown_removed_cards # New argument
            )
        except Exception as e_root: logger.error(f"Failed to create MCTS root node: {e_root}", exc_info=True); return None
        start_mcts_time = time.time(); num_simulations = 0; pool: Optional[multiprocessing.Pool] = None
        try:
            if self.num_workers > 1:
                 try: pool = multiprocessing.Pool(processes=self.num_workers); logger.debug(f"Created MP pool with {self.num_workers} workers.")
                 except Exception as e_pool_create: logger.error(f"Failed to create MP pool: {e_pool_create}. Falling back to 1 worker.", exc_info=True); self.num_workers = 1
            while time.time() - start_mcts_time < self.time_limit:
                path, leaf_node = self._select(root_node, cards_just_dealt)
                if leaf_node is None: logger.warning("Selection phase returned None leaf node."); break
                node_to_rollout_from = leaf_node
                if not leaf_node.is_terminal():
                    if leaf_node.untried_next_states: # Should be None if not expanded yet, or list
                        expanded_node = leaf_node.expand()
                        if expanded_node: node_to_rollout_from = expanded_node; path.append(expanded_node)
                rollout_results: List[Tuple[float, List[Dict[str, Any]]]] = []
                try:
                    board_to_sim = node_to_rollout_from.board
                    deck_to_sim = list(node_to_rollout_from.remaining_deck)
                    num_unknown_for_rollout = node_to_rollout_from.num_unknown_removed_cards
                    board_dict = {'rows': {r: Card.hand_to_str(cards) for r, cards in board_to_sim.rows.items()}, '_cards_placed': board_to_sim.get_total_cards()}

                    # Task now includes num_unknown_removed_cards
                    rollout_task_item = (board_dict, deck_to_sim, num_unknown_for_rollout)

                    if pool and self.num_workers > 1:
                         # Each task for apply_async should be a tuple of args for run_parallel_rollout
                         async_results = [pool.apply_async(run_parallel_rollout, rollout_task_item) for _ in range(self.rollouts_per_leaf)]
                         for res in async_results:
                              try:
                                   timeout_get = max(0.5, self.time_limit * 0.1) # Timeout for getting result
                                   reward, actions = res.get(timeout=timeout_get)
                                   rollout_results.append((reward, actions)); num_simulations += 1
                              except multiprocessing.TimeoutError: logger.warning("Rollout worker timed out.")
                              except Exception as e_get: logger.warning(f"Error getting result from worker: {e_get}")
                    else:
                         # Sequential execution
                         for _ in range(self.rollouts_per_leaf):
                              try:
                                   # Directly use the components of rollout_task_item
                                   reward, actions = run_parallel_rollout(board_dict, deck_to_sim, num_unknown_for_rollout)
                                   rollout_results.append((reward, actions)); num_simulations += 1
                              except Exception as e_seq: logger.warning(f"Error during sequential rollout: {e_seq}", exc_info=True)
                except Exception as e_roll: logger.error(f"Error preparing/running rollout phase: {e_roll}", exc_info=True)
                if rollout_results:
                    for reward, actions in rollout_results:
                         self._backpropagate_standard(path, reward)
                         self._backpropagate_rave(path, actions, reward)
        except KeyboardInterrupt:
             logger.warning("MCTS execution interrupted by user.")
             if pool:
                 try:
                     pool.terminate()
                     pool.join()
                 except Exception:
                     pass # Ignore errors during cleanup on interrupt
             return None
        except Exception as e_mcts:
            logger.error(f"Critical error during MCTS execution: {e_mcts}", exc_info=True)
            if pool:
                try:
                    pool.terminate()
                    pool.join()
                except Exception:
                    pass # Ignore errors during cleanup on critical error
            return None
        finally:
            if pool:
                try:
                    pool.close()
                    pool.join()
                except Exception as e_pool_close:
                    logger.error(f"Error closing MCTS pool: {e_pool_close}")

        elapsed_time = time.time() - start_mcts_time
        sims_per_sec = (num_simulations / elapsed_time) if elapsed_time > 0 else 0
        logger.info(f"MCTS finished: Ran {num_simulations} simulations in {elapsed_time:.3f}s ({sims_per_sec:.1f} sims/s). Root visits: {root_node.visits}")
        logger.info(f"Transposition Table: Hits={self.cache_hits}, Misses={self.cache_misses}")
        best_placement_info = self._select_best_placement(root_node, cards_just_dealt)
        total_time = time.time() - start_time_total
        logger.info(f"--- AI Agent: Placement chosen in {total_time:.3f}s ---")
        return best_placement_info

    def _select(self, root_node: MCTSNode, initial_cards_for_root: List[int]) -> Tuple[List[MCTSNode], Optional[MCTSNode]]:
        path = [root_node]; current_node = root_node
        while True:
            # Transposition Table lookup
            cache_key = (current_node.board.get_board_state_tuple(), frozenset(current_node.remaining_deck), current_node.num_unknown_removed_cards)
            if cache_key in self.transposition_table:
                self.cache_hits += 1
                if current_node.visits == 0: # Only load if the node object is "new" for this path
                    cached_data = self.transposition_table[cache_key]
                    current_node.visits = cached_data['visits']
                    current_node.total_reward = cached_data['total_reward']
                    current_node.rave_visits = cached_data['rave_visits']
                    current_node.rave_reward = cached_data['rave_reward']
            else:
                self.cache_misses += 1

            if current_node.is_terminal(): return path, current_node
            if current_node.untried_next_states is None: # Node has not been expanded before in this specific path/object
                cards_to_generate_for: List[int]
                if current_node is root_node: cards_to_generate_for = initial_cards_for_root
                else:
                    cards_on_board = current_node.board.get_total_cards()
                    # ИСПРАВЛЕНО: Логика num_cards_to_deal_next
                    if cards_on_board >= PlayerBoard.TOTAL_CAPACITY - 2 : # Если осталось 0, 1 или 2 карты до конца
                        num_cards_to_deal_next = 0 # Больше не раздаем, если почти конец
                    elif cards_on_board == 0: # Первая улица
                        num_cards_to_deal_next = 5 # Это не должно происходить здесь, т.к. initial_cards_for_root для корня
                    else: # Остальные улицы
                        num_cards_to_deal_next = 3

                    cards_to_generate_for = []
                    if num_cards_to_deal_next > 0:
                        if len(current_node.remaining_deck) >= num_cards_to_deal_next:
                            try: cards_to_generate_for = random.sample(list(current_node.remaining_deck), num_cards_to_deal_next)
                            except ValueError: logger.warning(f"Not enough cards in deck for node {current_node}"); cards_to_generate_for = []
                            except Exception as e_sample: logger.error(f"Error sampling cards: {e_sample}"); cards_to_generate_for = []
                        else: cards_to_generate_for = [] # Недостаточно карт в колоде
                current_node.untried_next_states = current_node._generate_next_states(cards_to_generate_for) if cards_to_generate_for else []
                if not current_node.untried_next_states and not current_node.children: return path, current_node # Лист, нет ходов
            if current_node.untried_next_states: # Если есть неиспробованные состояния
                num_children = len(current_node.children)
                allowed_children = PW_C * math.pow(current_node.visits + 1, PW_ALPHA)
                if num_children < allowed_children: # Прогрессивное расширение
                    return path, current_node # Возвращаем узел для расширения
                else: pass # Продолжаем выбор по UCT, если PW не разрешает расширение
            if not current_node.children: return path, current_node # Лист, если нет детей после PW проверки
            selected_child = current_node.uct_select_child(self.exploration)
            if selected_child is None:
                if current_node.children: selected_child = random.choice(list(current_node.children.values())) # Fallback
                if selected_child is None: return path, current_node # Не удалось выбрать ребенка
            current_node = selected_child; path.append(current_node)

    def _backpropagate_standard(self, path: List[MCTSNode], reward: float):
        if not path: return
        for node in reversed(path):
            node.visits += 1
            node.total_reward += reward
            # Store/Update node info in transposition table
            cache_key = (node.board.get_board_state_tuple(), frozenset(node.remaining_deck), node.num_unknown_removed_cards)
            self.transposition_table[cache_key] = {
                'visits': node.visits,
                'total_reward': node.total_reward,
                'rave_visits': node.rave_visits,
                'rave_reward': node.rave_reward
            }

    def _backpropagate_rave(self, path: List[MCTSNode], simulation_actions: List[Dict[str, Any]], reward: float):
        sim_action_keys = set()
        for action in simulation_actions:
            placements = action.get('placements')
            if placements:
                 try:
                     # Ключ должен быть кортежем из кортежей (карта, ряд, индекс), отсортированных
                     action_key = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placements]))
                     sim_action_keys.add(action_key)
                 except Exception as e: logger.warning(f"RAVE Backprop Key Error: {e}")
        if not sim_action_keys: return
        for node_in_path in path:
            for child_key, child_node in node_in_path.children.items():
                if child_key in sim_action_keys:
                    child_node.rave_visits += 1; child_node.rave_reward += reward

    def _select_best_placement(self,
                               root_node: MCTSNode,
                               initial_cards_dealt: List[int]) -> Optional[Dict[str, Any]]:
        if not root_node.children:
            logger.warning("No children found at root node. Cannot select best placement.")
            # Попытка вернуть первое сгенерированное состояние, если дети не были исследованы
            if hasattr(root_node, '_generated_states_for_expand') and root_node._generated_states_for_expand:
                 try:
                     first_key = next(iter(root_node._generated_states_for_expand))
                     _, _, placement_info = root_node._generated_states_for_expand[first_key]
                     logger.warning("Returning first generated placement as fallback (no children explored).")
                     return placement_info
                 except Exception as e_fallback: logger.error(f"Error during fallback: {e_fallback}"); return None
            return None

        is_first_street = (root_node.board.get_total_cards() == 0 and len(initial_cards_dealt) == 5)
        trip_in_hand_rank = -1
        if is_first_street:
            ranks = [Card.get_rank_int(c) for c in initial_cards_dealt if c is not None]
            rank_counts = Counter(ranks)
            trip_in_hand_rank = next((rank for rank, count in rank_counts.items() if count >= 3), -1)
            if trip_in_hand_rank != -1: logger.info(f"Rule Check: First street with trip of rank {trip_in_hand_rank} detected.")

        child_stats: List[Tuple[Dict[str, Any], int, float, int, float]] = []
        items = list(root_node.children.items())
        
        logger.info(f"--- Evaluating {len(items)} child nodes for final placement ---")
        for placement_key, child_node in items:
             avg_reward = child_node.total_reward / child_node.visits if child_node.visits > 0 else -float('inf')
             rave_avg_reward = child_node.rave_reward / child_node.rave_visits if child_node.rave_visits > 0 else -float('inf')
             p_info = getattr(child_node, 'placement_info', None)
             if p_info is None: logger.warning(f"Child node {child_node} missing placement_info."); continue
             child_stats.append((p_info, child_node.visits, avg_reward, child_node.rave_visits, rave_avg_reward))
        
        # ИСПРАВЛЕНО: Сортировка по avg_reward, затем visits (как в комментарии)
        child_stats.sort(key=lambda x: (x[2], x[1]), reverse=True)

        LOG_TOP_N_OPTIONS = 5
        logger.info(f"Top {min(LOG_TOP_N_OPTIONS, len(child_stats))} placement options considered by AI:")
        for i, (p_info, visits, avg_r, rave_v, rave_r) in enumerate(child_stats[:LOG_TOP_N_OPTIONS]):
            log_placements = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_info.get('placements', [])])
            log_discard = f"(D: {Card.to_str(p_info.get('discarded'))})" if p_info.get('discarded') else ""
            logger.info(f"  #{i+1}: V={visits:<5} AvgR={avg_r:<7.2f} | RV={rave_v:<5} RaveR={rave_r:<7.2f} -> {log_placements:<35} {log_discard:<7}")

        best_allowed_placement: Optional[Dict[str, Any]] = None
        for placement_info, visits, avg_reward, rave_visits, rave_avg_reward in child_stats:
            skip_placement = False
            if is_first_street and trip_in_hand_rank != -1:
                placements = placement_info.get('placements', [])
                for card_int, row_name, index in placements:
                    current_card_rank = Card.get_rank_int(card_int) if card_int else -1
                    if current_card_rank == trip_in_hand_rank and row_name == 'top':
                        log_placements_skip = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in placements])
                        logger.warning(f"Rule Violation: Skipping placement {log_placements_skip} (Trip rank {trip_in_hand_rank} on Top).")
                        skip_placement = True; break
            if not skip_placement:
                best_allowed_placement = placement_info
                log_placements_sel = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in best_allowed_placement.get('placements', [])])
                log_discard_sel = f"(D: {Card.to_str(best_allowed_placement.get('discarded'))})" if best_allowed_placement.get('discarded') else ""
                logger.info(f"==> AI Selected Placement (V={visits}, AvgR={avg_reward:.2f}): {log_placements_sel} {log_discard_sel}")
                return best_allowed_placement

        if best_allowed_placement is None: # Если все варианты были отфильтрованы правилом
            logger.warning("All evaluated placements were disallowed by rules or no valid children available.")
            if child_stats: # Если были какие-то варианты, но все нарушили правило
                 logger.warning("Returning the most promising (highest avg_reward) placement despite potential rule violation as fallback.")
                 return child_stats[0][0] # Возвращаем самый первый (лучший по награде) из отсортированных
            logger.error("No children stats available to select a fallback placement.")
            return None
        # Эта строка не должна достигаться, если логика выше верна, но для безопасности:
        return best_allowed_placement
