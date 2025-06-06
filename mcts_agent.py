# mcts_agent.py v2.8 (Simplified placement count logic)
"""
Реализация MCTS-агента для задачи размещения НАБОРА карт OFC Pineapple.
- Адаптирован для обработки ситуации, когда карт сдано больше, чем доступных слотов.
- Усилен фокус на достижение Фантазии в эвристиках (через MCTSNode).
- Корректно передает num_unknown_removed_cards.
- Улучшено логирование и выбор лучшего хода.
- Упрощена логика определения количества размещаемых карт.
"""

import time
import random
import multiprocessing
import traceback
import sys
import logging
import math
from typing import Optional, Any, List, Tuple, Set, Dict
from collections import Counter, defaultdict

try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, STR_RANKS # Добавлен STR_RANKS для логгирования
    # HEURISTIC_FOUL_PENALTY импортируется из mcts_node, где он определен
    from mcts_node import MCTSNode, run_parallel_rollout, RAVE_K, PW_C, PW_ALPHA, HEURISTIC_FOUL_PENALTY
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, RANK_QUEEN, RANK_KING, RANK_ACE # Добавлены RANK_ константы
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
except ImportError as e:
    logging.critical(f"Failed to import modules in mcts_agent.py: {e}")
    class PlayerBoard: TOTAL_CAPACITY = 13; pass # type: ignore
    class Card: pass # type: ignore
    class Deck: FULL_DECK_CARDS = set() # type: ignore
    class MCTSNode: pass # type: ignore
    def run_parallel_rollout(*args): return 0.0, [] # type: ignore
    RAVE_K = 500.0; PW_C = 2.0; PW_ALPHA = 0.5; HEURISTIC_FOUL_PENALTY = -1000.0 # type: ignore
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid") # type: ignore
    WORST_RANK = 9999; WORST_CLASS = 9 # type: ignore
    def check_board_foul(*args): return False # type: ignore
    def get_row_royalty(*args): return 0 # type: ignore
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {}; STR_RANKS = ""; RANK_QUEEN=10;RANK_KING=11;RANK_ACE=12 # type: ignore
    class MockEvaluator5Card: evaluate = lambda s, c: 9999 # type: ignore
    evaluator_5card = MockEvaluator5Card() # type: ignore
    raise ImportError("Missing core logic/node/evaluator modules for MCTSAgent") from e

logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class MCTSAgent:
    DEFAULT_EXPLORATION: float = 1.414 
    DEFAULT_TIME_LIMIT_MS: int = 5000
    DEFAULT_NUM_WORKERS: int = max(1, multiprocessing.cpu_count() - 1 if multiprocessing.cpu_count() > 1 else 1)
    DEFAULT_ROLLOUTS_PER_LEAF: int = 15 # Немного увеличено

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

        self.transposition_table: Dict[Tuple, Dict[str, Any]] = {}
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.last_simulation_count: int = 0

        logger.info(f"MCTS Agent initialized: TimeLimit={self.time_limit:.2f}s, Exploration={self.exploration}, "
                    f"Workers={self.num_workers}, RolloutsPerLeaf={self.rollouts_per_leaf}, RAVE_K={RAVE_K}")
        try: # Установка метода start для multiprocessing
            current_method = multiprocessing.get_start_method(allow_none=True)
            if sys.platform == "win32": # На Windows 'spawn' часто является единственным безопасным методом
                if current_method != 'spawn':
                    try: multiprocessing.set_start_method('spawn', force=True); logger.info("Set MP start method to 'spawn' for Windows.")
                    except RuntimeError: logger.warning("MP start method already set on Windows, cannot change to 'spawn'.")
            elif current_method != 'spawn': # Для других ОС тоже пробуем 'spawn' для консистентности
                available_methods = multiprocessing.get_all_start_methods()
                if 'spawn' in available_methods:
                    try: multiprocessing.set_start_method('spawn', force=True); logger.info("Set MP start method to 'spawn'.")
                    except RuntimeError: logger.warning("MP start method already set, cannot change to 'spawn'.")
                elif 'forkserver' in available_methods: # 'forkserver' как альтернатива
                     try: multiprocessing.set_start_method('forkserver', force=True); logger.info("Set MP start method to 'forkserver'.")
                     except RuntimeError: logger.warning("MP start method already set, cannot change to 'forkserver'.")
                else: logger.warning(f"'spawn' or 'forkserver' MP start method not available. Current: {current_method}")
        except Exception as e: logger.warning(f"Error checking/setting MP start method: {e}.")


    def choose_placement(self,
                         initial_board: PlayerBoard,
                         cards_just_dealt: List[int],
                         current_remaining_deck: Set[int],
                         num_unknown_removed_cards: int = 0) -> Optional[Dict[str, Any]]:
        start_time_total = time.time()
        self.cache_hits = 0; self.cache_misses = 0; self.last_simulation_count = 0

        if not cards_just_dealt:
            logger.warning("MCTSAgent: choose_placement called with no cards dealt.")
            return None

        num_dealt = len(cards_just_dealt)
        cards_on_board_start = initial_board.get_total_cards()
        available_slots = PlayerBoard.TOTAL_CAPACITY - cards_on_board_start
        
        # FIX: Упрощенная и более надежная логика определения количества размещаемых карт
        if cards_on_board_start == 0:
            num_to_place = 5 # Первая улица, размещаем 5
        else:
            num_to_discard = 1 if num_dealt > 1 else 0
            num_to_place = num_dealt - num_to_discard
        
        num_cards_ai_will_target_to_place = min(num_to_place, available_slots)

        logger.info(f"\n--- AI Agent: Choosing placement ---")
        logger.info(f"Initial Board ({cards_on_board_start} cards):\n{initial_board}")
        logger.info(f"Cards Dealt ({num_dealt}): {[Card.to_str(c) for c in cards_just_dealt]}")
        logger.info(f"Available slots: {available_slots}. AI will target to place: {num_cards_ai_will_target_to_place} card(s).")
        logger.info(f"Remaining deck size (for AI): {len(current_remaining_deck)}")
        logger.info(f"Number of unknown cards removed: {num_unknown_removed_cards}")

        if available_slots <= 0: # Нет места на доске
            if num_dealt > 0:
                 logger.warning(f"No available slots on board, but {num_dealt} cards dealt. Discarding all.")
                 discard_val: Any = tuple(sorted(cards_just_dealt)) if len(cards_just_dealt) > 1 else cards_just_dealt[0]
                 return {'placements': [], 'discarded': discard_val, 'score': HEURISTIC_FOUL_PENALTY, 'reason': "No slots, discarding all."}
            else: return None

        if num_cards_ai_will_target_to_place <= 0 and num_dealt > 0: # Нечего размещать, но карты есть -> сброс всех
            logger.info(f"Target to place is {num_cards_ai_will_target_to_place}, but {num_dealt} cards dealt. Discarding all.")
            discard_val: Any = tuple(sorted(cards_just_dealt)) if len(cards_just_dealt) > 1 else cards_just_dealt[0]
            return {'placements': [], 'discarded': discard_val, 'score': HEURISTIC_FOUL_PENALTY / 2, 'reason': "Target place is <=0, discarding all."}


        actual_cards_to_consider_for_root = cards_just_dealt

        try:
            root_node = MCTSNode(
                board=initial_board.copy(), remaining_deck=current_remaining_deck.copy(),
                parent=None, placement_info=None, num_unknown_removed_cards=num_unknown_removed_cards
            )
        except Exception as e_root: logger.error(f"Failed to create MCTS root node: {e_root}", exc_info=True); return None

        start_mcts_time = time.time(); num_simulations_total_loop = 0; pool: Optional[multiprocessing.Pool] = None
        
        try:
            if self.num_workers > 1:
                 try:
                     pool = multiprocessing.Pool(processes=self.num_workers)
                 except Exception as e_pool_create:
                     logger.error(f"MP Pool creation failed: {e_pool_create}. Fallback to 1 worker.", exc_info=True)
                     self.num_workers = 1

            while time.time() - start_mcts_time < self.time_limit:
                path, leaf_node = self._select(root_node, actual_cards_to_consider_for_root)
                if leaf_node is None: logger.warning("Selection returned None leaf node."); break
                node_to_rollout_from = leaf_node
                if not leaf_node.is_terminal():
                    if leaf_node.untried_next_states or not leaf_node.children: # Условие для вызова expand
                        expanded_node = leaf_node.expand()
                        if expanded_node: node_to_rollout_from = expanded_node; path.append(expanded_node)
                
                rollout_results: List[Tuple[float, List[Dict[str, Any]]]] = []
                try:
                    board_to_sim = node_to_rollout_from.board
                    deck_to_sim = list(node_to_rollout_from.remaining_deck)
                    num_unknown_for_rollout = node_to_rollout_from.num_unknown_removed_cards
                    board_dict = {'rows': {r: Card.hand_to_str(cards) for r, cards in board_to_sim.rows.items()}, '_cards_placed': board_to_sim.get_total_cards()}
                    rollout_task_item = (board_dict, deck_to_sim, num_unknown_for_rollout)
                    current_batch_sims = 0
                    if pool and self.num_workers > 1:
                         async_results = [pool.apply_async(run_parallel_rollout, rollout_task_item) for _ in range(self.rollouts_per_leaf)]
                         for res in async_results:
                              try:
                                   reward, actions = res.get(timeout=max(0.5, self.time_limit * 0.1))
                                   rollout_results.append((reward, actions)); current_batch_sims += 1
                              except multiprocessing.TimeoutError: logger.warning("Rollout worker timed out.")
                              except Exception as e_get: logger.warning(f"Error getting result from worker: {e_get}")
                    else:
                         for _ in range(self.rollouts_per_leaf):
                              try:
                                   reward, actions = run_parallel_rollout(board_dict, deck_to_sim, num_unknown_for_rollout)
                                   rollout_results.append((reward, actions)); current_batch_sims += 1
                              except Exception as e_seq: logger.warning(f"Error during sequential rollout: {e_seq}", exc_info=True)
                    num_simulations_total_loop += current_batch_sims
                except Exception as e_roll: logger.error(f"Error preparing/running rollout: {e_roll}", exc_info=True)
                
                if rollout_results:
                    for reward, actions_hist_rollout in rollout_results: # actions_hist_rollout - это список словарей placement_info
                         self._backpropagate_standard(path, reward)
                         self._backpropagate_rave(path, actions_hist_rollout, reward) # Передаем историю действий из роллаута
        except KeyboardInterrupt:
             logger.warning("MCTS interrupted.")
             if pool:
                 try:
                     pool.terminate()
                     pool.join()
                 except Exception:
                     pass
             return None
        except Exception as e_mcts:
            logger.error(f"Critical MCTS error: {e_mcts}", exc_info=True)
            if pool:
                try:
                    pool.terminate()
                    pool.join()
                except Exception:
                    pass
            return None
        finally:
            if pool:
                try:
                    pool.close()
                    pool.join()
                except Exception as e_pool_close:
                    logger.error(f"Error closing MCTS pool: {e_pool_close}")

        self.last_simulation_count = num_simulations_total_loop
        elapsed_time = time.time() - start_mcts_time
        sims_per_sec = (self.last_simulation_count / elapsed_time) if elapsed_time > 0 else 0
        logger.info(f"MCTS finished: {self.last_simulation_count} sims in {elapsed_time:.3f}s ({sims_per_sec:.1f} sims/s). Root visits: {root_node.visits if root_node else 'N/A'}")
        logger.info(f"TT: Hits={self.cache_hits}, Misses={self.cache_misses}")
        
        best_placement_info = self._select_best_placement(root_node, actual_cards_to_consider_for_root, available_slots)
        
        total_time = time.time() - start_time_total
        logger.info(f"--- AI Agent: Placement chosen in {total_time:.3f}s ---")
        return best_placement_info

    def _select(self, root_node: MCTSNode, initial_cards_for_root: List[int]) -> Tuple[List[MCTSNode], Optional[MCTSNode]]:
        path = [root_node]; current_node = root_node
        while True:
            cache_key = (current_node.board.get_board_state_tuple(), frozenset(current_node.remaining_deck), current_node.num_unknown_removed_cards)
            if cache_key in self.transposition_table:
                self.cache_hits += 1
                if current_node.visits == 0: # Загружаем только если узел "новый" для этого пути
                    cached_data = self.transposition_table[cache_key]
                    current_node.visits = cached_data.get('visits', 0)
                    current_node.total_reward = cached_data.get('total_reward', 0.0)
                    # RAVE статы должны быть defaultdict, чтобы .get не вызывал ошибку, если ключа нет
                    current_node.rave_visits_count = cached_data.get('rave_visits_count', 0)
                    current_node.rave_total_reward = cached_data.get('rave_total_reward', 0.0)
            else: self.cache_misses += 1

            if current_node.is_terminal(): return path, current_node
            
            # Генерация состояний, если узел еще не был полностью исследован на предмет начальных ходов
            if current_node.untried_next_states is None and not current_node._generated_states_for_expand:
                cards_to_generate_for: List[int]
                if current_node is root_node: cards_to_generate_for = initial_cards_for_root
                else: # Логика для не-корневых узлов (ходы в симуляции)
                    cards_on_board = current_node.board.get_total_cards()
                    available_slots_node = PlayerBoard.TOTAL_CAPACITY - cards_on_board
                    num_cards_to_deal_next = 0
                    if available_slots_node > 0: # Если есть куда ставить
                        if cards_on_board == 0: num_cards_to_deal_next = 5 # Не должно быть здесь
                        else: num_cards_to_deal_next = 3 # Стандартная раздача 3 карт
                    
                    cards_to_generate_for = []
                    if num_cards_to_deal_next > 0:
                        if len(current_node.remaining_deck) >= num_cards_to_deal_next:
                            try: cards_to_generate_for = random.sample(list(current_node.remaining_deck), num_cards_to_deal_next)
                            except ValueError: logger.debug(f"Select: Not enough cards in deck for node {current_node}"); cards_to_generate_for = []
                        # else: logger.debug(f"Select: Not enough cards in deck ({len(current_node.remaining_deck)}) to deal {num_cards_to_deal_next}")
                
                # _generate_next_states заполнит _generated_states_for_expand
                current_node.untried_next_states = current_node._generate_next_states(cards_to_generate_for) 
                if not current_node._generated_states_for_expand and not current_node.children:
                    return path, current_node # Лист, если нет сгенерированных ходов

            has_unexpanded_action = any(key not in current_node.children for key in current_node._generated_states_for_expand)
            if has_unexpanded_action:
                num_children = len(current_node.children)
                allowed_children_pw = PW_C * math.pow(current_node.visits + 1, PW_ALPHA)
                if num_children < allowed_children_pw or (num_children == 0 and current_node._generated_states_for_expand):
                    return path, current_node # Возвращаем узел для вызова expand()

            if not current_node.children: return path, current_node 
            
            selected_child = current_node.uct_select_child(self.exploration)
            if selected_child is None:
                logger.warning(f"UCT select child returned None for node with {len(current_node.children)} children. V={current_node.visits}. Forcing random choice.")
                if current_node.children: selected_child = random.choice(list(current_node.children.values()))
                if selected_child is None: return path, current_node # Все еще None - проблема
            current_node = selected_child; path.append(current_node)

    def _backpropagate_standard(self, path: List[MCTSNode], reward: float):
        if not path: return
        for node in reversed(path):
            node.visits += 1; node.total_reward += reward
            cache_key = (node.board.get_board_state_tuple(), frozenset(node.remaining_deck), node.num_unknown_removed_cards)
            # Обновляем или создаем запись в ТТ
            entry = self.transposition_table.get(cache_key, {})
            entry['visits'] = node.visits
            entry['total_reward'] = node.total_reward
            entry['rave_visits_count'] = node.rave_visits_count # Сохраняем RAVE статы
            entry['rave_total_reward'] = node.rave_total_reward
            self.transposition_table[cache_key] = entry


    def _backpropagate_rave(self, path: List[MCTSNode], simulation_actions_history: List[Dict[str, Any]], reward: float):
        # simulation_actions_history - это список словарей placement_info из роллаута
        sim_action_keys_in_rollout = set()
        for action_info in simulation_actions_history: # action_info это dict {'placements': [...], 'discarded': ...}
            placements = action_info.get('placements')
            discarded = action_info.get('discarded')
            if placements: # Может быть пустым, если действие - только сброс
                 try:
                     placement_tuples = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in placements]))
                     action_key_from_sim = (placement_tuples, discarded)
                     sim_action_keys_in_rollout.add(action_key_from_sim)
                 except Exception as e: logger.warning(f"RAVE Backprop: Error creating key from sim_action {action_info}: {e}")
            elif discarded is not None: # Действие - только сброс
                 action_key_from_sim = (tuple(), discarded) # Пустой кортеж для размещений
                 sim_action_keys_in_rollout.add(action_key_from_sim)

        if not sim_action_keys_in_rollout: return

        for node_in_path in path: # Для каждого узла на пути от корня до листа, с которого начался роллаут
            for child_action_key, child_node in node_in_path.children.items():
                # child_action_key это ключ действия, ведущего к child_node
                if child_action_key in sim_action_keys_in_rollout:
                    # Если действие, ведущее к этому ребенку, было совершено где-то в симуляции (AMAF)
                    child_node.rave_visits_count += 1
                    child_node.rave_total_reward += reward

    def _select_best_placement(self,
                               root_node: MCTSNode,
                               initial_cards_dealt: List[int],
                               available_slots_on_board: int) -> Optional[Dict[str, Any]]:
        if not root_node or not root_node.children:
            logger.warning("No children at root. Cannot select best placement.")
            if root_node and hasattr(root_node, '_generated_states_for_expand') and root_node._generated_states_for_expand:
                 try:
                     best_h_action = None; best_h_score = -float('inf')
                     for _, (_, _, p_info) in root_node._generated_states_for_expand.items():
                         if p_info.get('score', -float('inf')) > best_h_score:
                             best_h_score = p_info['score']; best_h_action = p_info
                     if best_h_action: logger.warning("Returning best heuristic placement (MCTS no explore)."); return best_h_action
                 except Exception as e_fall: logger.error(f"Fallback selection error: {e_fall}"); return None
            return None

        is_first_street = (root_node.board.get_total_cards() == 0) # Упрощенная проверка
        trip_in_hand_rank = -1
        if is_first_street and len(initial_cards_dealt) == 5: # Только для 5 карт на 1й улице
            ranks = [Card.get_rank_int(c) for c in initial_cards_dealt if c is not None]
            rank_counts = Counter(ranks)
            trip_in_hand_rank = next((rank for rank, count in rank_counts.items() if count >= 3), -1)
            if trip_in_hand_rank != -1: logger.info(f"Rule Check: First street with trip of {STR_RANKS[trip_in_hand_rank]} detected.")

        child_stats: List[Tuple[Dict[str, Any], int, float, int, float]] = [] # p_info, visits, avg_r, rave_v, rave_r
        
        logger.info(f"--- Evaluating {len(root_node.children)} child nodes for final placement ---")
        for _, child_node in root_node.children.items():
             p_info = child_node.placement_info
             if p_info is None: logger.warning(f"Child node missing placement_info."); continue
             avg_reward = child_node.total_reward / child_node.visits if child_node.visits > 0 else -float('inf')
             rave_avg_reward = child_node.rave_total_reward / child_node.rave_visits_count if child_node.rave_visits_count > 0 else avg_reward # Fallback to avg_reward if no RAVE
             child_stats.append((p_info, child_node.visits, avg_reward, child_node.rave_visits_count, rave_avg_reward))
        
        child_stats.sort(key=lambda x: (x[2], x[1], x[4]), reverse=True) # Sort by AvgReward, then Visits, then RaveAvgReward

        LOG_TOP_N = 5
        logger.info(f"Top {min(LOG_TOP_N, len(child_stats))} placement options by AI (Sorted by AvgR, V, RaveR):")
        for i, (p_info, v, avg_r, rv, rave_r) in enumerate(child_stats[:LOG_TOP_N]):
            pl_str = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in p_info.get('placements', [])])
            disc_obj = p_info.get('discarded'); disc_str = ""
            if disc_obj is not None: disc_str = f"(D: {Card.to_str(disc_obj) if isinstance(disc_obj,int) else ','.join(Card.to_str(c) for c in disc_obj)})"
            logger.info(f"  #{i+1}: V={v:<5} AvgR={avg_r:<7.2f} | RV={rv:<5} RaveR={rave_r:<7.2f} -> {pl_str:<35} {disc_str:<10}")

        best_allowed_placement: Optional[Dict[str, Any]] = None
        for p_info_cand, visits_cand, avg_r_cand, _, _ in child_stats:
            skip = False
            placements_cand = p_info_cand.get('placements', [])
            if is_first_street and trip_in_hand_rank != -1:
                if any(Card.get_rank_int(pc) == trip_in_hand_rank and pr == 'top' for pc, pr, _ in placements_cand):
                    logger.warning(f"Filter: Skip trip {STR_RANKS[trip_in_hand_rank]} on Top: {p_info_cand}")
                    skip = True
            
            if not skip:
                # FIX: Упрощенная и более надежная логика определения ожидаемого количества карт
                num_expected_to_place = 0
                if root_node.board.get_total_cards() == 0:
                    num_expected_to_place = 5
                else:
                    num_to_discard = 1 if len(initial_cards_dealt) > 1 else 0
                    num_expected_to_place = min(len(initial_cards_dealt) - num_to_discard, available_slots_on_board)

                num_placed_cand = len(placements_cand)
                if num_placed_cand != num_expected_to_place:
                     logger.error(f"Filter Error: Candidate places {num_placed_cand}, expected {num_expected_to_place}. P_info: {p_info_cand}")
                     continue

                best_allowed_placement = p_info_cand
                pl_sel_str = ", ".join([f"{Card.to_str(p[0])}@{p[1]}[{p[2]}]" for p in best_allowed_placement.get('placements', [])])
                disc_obj_sel = best_allowed_placement.get('discarded'); disc_sel_str = ""
                if disc_obj_sel is not None: disc_sel_str = f"(D: {Card.to_str(disc_obj_sel) if isinstance(disc_obj_sel,int) else ','.join(Card.to_str(c) for c in disc_obj_sel)})"
                logger.info(f"==> AI Selected (V={visits_cand}, AvgR={avg_r_cand:.2f}): {pl_sel_str} {disc_sel_str}")
                return best_allowed_placement

        if not best_allowed_placement:
            logger.warning("All placements filtered or no children. Fallback to first sorted (if any).")
            if child_stats:
                 fallback_cand = child_stats[0][0]
                 num_placed_fallback = len(fallback_cand.get('placements', []))
                 
                 num_expected_to_place_fb = 0
                 if root_node.board.get_total_cards() == 0:
                     num_expected_to_place_fb = 5
                 else:
                     num_to_discard_fb = 1 if len(initial_cards_dealt) > 1 else 0
                     num_expected_to_place_fb = min(len(initial_cards_dealt) - num_to_discard_fb, available_slots_on_board)

                 if num_placed_fallback == num_expected_to_place_fb:
                     logger.warning(f"Using fallback (might violate rules): {fallback_cand}")
                     return fallback_cand
                 else:
                     logger.error(f"Fallback candidate places {num_placed_fallback}, expected {num_expected_to_place_fb}. No valid move.")
                     return None
            logger.error("No children stats for fallback. No valid move found.")
            return None
        
        return best_allowed_placement # Should be unreachable
