# mcts_node.py v2.8.3
# ИСПРАВЛЕНО: Добавлены определения для MAX_PERMUTATIONS_STREET_1 и MAX_PERMUTATIONS_STREET_N
# ИЗМЕНЕНО: Уровень логгера по умолчанию на INFO
"""
Узел MCTS и логика симуляции для OFC Pineapple.
"""
import random
import math # Добавлен math
import logging
from typing import List, Tuple, Dict, Optional, Set, Any, cast
from collections import Counter, defaultdict
# import itertools # Закомментировано, так как не используется напрямую в этом файле

try:
    from ofc_logic import PlayerBoard, Card, Deck, RANK_MAP, card_to_str, hand_to_str
    from ofc_evaluators import (
        get_hand_rank_safe, WORST_RANK, WORST_CLASS,
        check_board_foul, get_row_royalty,
        ROYALTY_TOP_PAIRS, calculate_total_royalty_for_board
    )
    from ofc_evaluator_5card import evaluator_5card_instance as evaluator_5card
    from ofc_evaluator_3card import evaluate_3_card_ofc, WORST_RANK_3CARD
except ImportError:
    logging.critical("Failed to import modules in mcts_node.py")
    class PlayerBoard: # type: ignore
        TOTAL_CAPACITY = 13
        def __init__(self, rows=None, cards_placed=0): self.rows = rows or {}; self._cards_placed = cards_placed
        def copy(self): return PlayerBoard(self.rows.copy(), self._cards_placed)
        def add_card(self, card, row, index): pass
        def get_total_cards(self): return self._cards_placed
        def is_complete(self): return self._cards_placed == self.TOTAL_CAPACITY
        def get_available_slots(self): return []
        def get_row_cards(self, row_name: str) -> List[int]: return []
        def __str__(self): return "MockBoard"
    class Card: # type: ignore
        @staticmethod
        def from_str(s): return 0
        @staticmethod
        def to_str(c): return "??"
        @staticmethod
        def get_rank_int(c): return 0
        @staticmethod
        def get_suit_int(c): return 0
    class Deck: # type: ignore
        FULL_DECK_CARDS = set(range(1,53))
        def __init__(self, cards=None): self.cards = cards or []
        def deal(self, num): return []
        def get_cards(self): return []
    def get_hand_rank_safe(*args): return (9999, 9, "Invalid") # type: ignore
    WORST_RANK = 9999; WORST_CLASS = 9 # type: ignore
    def check_board_foul(*args): return False # type: ignore
    def get_row_royalty(*args): return 0 # type: ignore
    def calculate_total_royalty_for_board(*args): return 0 # type: ignore
    ROYALTY_TOP_PAIRS = {}; RANK_MAP = {} # type: ignore
    def card_to_str(c): return Card.to_str(c) # type: ignore
    def hand_to_str(h): return [Card.to_str(c) for c in h] # type: ignore
    class MockEvaluator5Card: evaluate = lambda s, c: 9999 # type: ignore
    evaluator_5card = MockEvaluator5Card() # type: ignore
    def evaluate_3_card_ofc(*args): return (9999, "Invalid", "XXX") # type: ignore
    WORST_RANK_3CARD = 999 # type: ignore
    raise ImportError("Missing core logic/evaluator modules for MCTSNode")


logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logger.setLevel(logging.INFO) # Уровень по умолчанию INFO
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Константы для MCTS ---
RAVE_K: float = 500.0  # Константа для RAVE, определяет баланс между UCB и RAVE оценками
PW_C: float = 2.0      # Константа для Progressive Widening (C)
PW_ALPHA: float = 0.5  # Константа для Progressive Widening (alpha)

# ИСПРАВЛЕНО: Добавлены определения для MAX_PERMUTATIONS_STREET_1 и MAX_PERMUTATIONS_STREET_N
# Эти значения являются предположениями и могут потребовать настройки.
# MAX_PERMUTATIONS_STREET_1: Максимальное количество перестановок для рассмотрения на первой улице (5 карт).
# 5! = 120. Если мы рассматриваем только размещения, а не выбор карт из руки, то это P(13,5) что очень много.
# Скорее всего, это ограничение на количество генерируемых *полных* размещений 5 карт.
MAX_PERMUTATIONS_STREET_1: int = 60 # Ограничим для производительности (например, топ N лучших по эвристике)
# MAX_PERMUTATIONS_STREET_N: Максимальное количество перестановок для последующих улиц (3 карты -> 2 разместить, 1 сбросить).
# 3 (выбор сброса) * P(num_empty_slots, 2).
# Если это просто количество полных действий (сброс + размещение 2 карт), то это 3 (сброс) * 2 (размещение) = 6.
MAX_PERMUTATIONS_STREET_N: int = 20 # Ограничим для производительности (например, топ N лучших по эвристике)


class MCTSNode:
    """ Узел в дереве MCTS. """
    def __init__(self, board: PlayerBoard, remaining_deck: Set[int],
                 parent: Optional['MCTSNode'] = None,
                 placement_info: Optional[Dict[str, Any]] = None):
        self.board: PlayerBoard = board
        self.remaining_deck: Set[int] = remaining_deck
        self.parent: Optional['MCTSNode'] = parent
        self.placement_info: Optional[Dict[str, Any]] = placement_info # Информация о размещении, которое привело к этому узлу

        self.children: Dict[Tuple, MCTSNode] = {} # Дочерние узлы {placement_key: MCTSNode}
        self.visits: int = 0
        self.total_reward: float = 0.0
        self.rave_visits: int = 0
        self.rave_reward: float = 0.0

        # Для Progressive Widening и RAVE
        self.untried_next_states: Optional[List[Tuple[PlayerBoard, Optional[int]]]] = None # (board_state, discarded_card_or_None)
        self._generated_states_for_expand: Dict[Tuple, Tuple[PlayerBoard, Optional[int], Dict[str, Any]]] = {} # key: (board, discarded, placement_info)

    def is_terminal(self) -> bool:
        return self.board.is_complete()

    def _generate_next_states(self, cards_just_dealt: List[int]) -> List[Tuple[PlayerBoard, Optional[int]]]:
        """
        Генерирует все возможные следующие состояния (доски и сброшенную карту) из текущего состояния
        для данного набора карт.
        Возвращает список кортежей (новая_доска, сброшенная_карта_int_или_None).
        """
        generated_states: List[Tuple[PlayerBoard, Optional[int]]] = []
        self._generated_states_for_expand = {} # Очищаем перед генерацией

        num_cards_on_board = self.board.get_total_cards()
        num_dealt = len(cards_just_dealt)

        if num_cards_on_board == 0: # Первая улица
            if num_dealt != 5:
                logger.error(f"Generate states: Expected 5 cards for street 1, got {num_dealt}")
                return []
            cards_to_place = cards_just_dealt
            card_to_discard = None
            num_to_place_on_board = 5
        elif num_cards_on_board < PlayerBoard.TOTAL_CAPACITY - 2 : # Улицы 2-4 (6, 8, 10, 12 карт на доске)
            if num_dealt != 3:
                logger.error(f"Generate states: Expected 3 cards for street 2-4, got {num_dealt}")
                return []
            num_to_place_on_board = 2
        elif num_cards_on_board == PlayerBoard.TOTAL_CAPACITY - 2: # Последняя улица, если осталось 2 места
             if num_dealt != 3: # Все еще получаем 3, но размещаем 2
                logger.error(f"Generate states: Expected 3 cards for final street (2 slots), got {num_dealt}")
                return []
             num_to_place_on_board = 2
        elif num_cards_on_board == PlayerBoard.TOTAL_CAPACITY - 1: # Последняя улица, если осталось 1 место
            # Это не должно происходить с текущей логикой раздачи по 3, т.к. всегда будет четное число карт для размещения
            logger.warning(f"Generate states: Odd number of cards to place for final street ({PlayerBoard.TOTAL_CAPACITY - num_cards_on_board} slots left). Dealt: {num_dealt}")
            # По правилам, если остался 1 слот, а раздали 3, то 1 размещаем, 2 сбрасываем.
            # Но MCTS агент должен передавать корректное число карт для размещения.
            # Этот блок скорее для полноты, текущая логика агента не должна сюда приводить.
            if num_dealt == 3: num_to_place_on_board = 1
            else: return [] # Некорректная ситуация
        else: # Доска полна или некорректное состояние
            return []

        available_slots_list = self.board.get_available_slots()
        if len(available_slots_list) < num_to_place_on_board:
            logger.warning(f"Not enough available slots ({len(available_slots_list)}) to place {num_to_place_on_board} cards.")
            return []

        # --- Логика генерации размещений ---
        # Это упрощенная версия, которая берет эвристически лучшие размещения
        # из _choose_best_heuristic_placement_v2, но _choose_best_heuristic_placement_v2
        # возвращает только ОДНО лучшее. Нам нужно несколько для MCTS.
        # Поэтому мы будем генерировать несколько вариантов.

        possible_placements_infos: List[Dict[str, Any]] = []

        if num_to_place_on_board == 5: # Первая улица
            # Для 5 карт, генерируем ограниченное число перестановок и размещений
            # Это очень сложная часть для полного перебора. Используем эвристику.
            # itertools.permutations(cards_just_dealt, 5) - сами карты
            # itertools.permutations(available_slots_list, 5) - слоты
            # Вместо полного перебора, можно использовать _choose_best_heuristic_placement_v2
            # несколько раз с разными "зашумленными" эвристиками или просто взять топ-N.
            # Пока что для простоты, если есть эвристический выбор, возьмем его.
            # В идеале, здесь должен быть более умный генератор нескольких хороших вариантов.
            heuristic_placement = MCTSNode._choose_best_heuristic_placement_v2(self.board, cards_just_dealt, self.remaining_deck)
            if heuristic_placement:
                possible_placements_infos.append(heuristic_placement)
            else: # Фоллбэк: случайное размещение, если эвристика не дала результат
                if len(available_slots_list) >= 5:
                    # Это очень грубый фоллбэк
                    slots_for_5 = random.sample(available_slots_list, 5)
                    placements = []
                    for i in range(5):
                        placements.append((cards_just_dealt[i], slots_for_5[i][0], slots_for_5[i][1]))
                    possible_placements_infos.append({'placements': placements, 'discarded': None})


        elif num_to_place_on_board == 2 and num_dealt == 3: # Улицы 2-5
            import itertools # Локальный импорт
            for i in range(num_dealt): # Перебираем, какую из 3 карт сбросить
                card_to_discard_val = cards_just_dealt[i]
                cards_to_place_val = [cards_just_dealt[j] for j in range(num_dealt) if j != i]

                # Генерируем все перестановки для размещения 2 карт в доступные слоты
                # Ограничиваем количество перестановок слотов
                if len(available_slots_list) >= 2:
                    slot_permutations = list(itertools.permutations(available_slots_list, 2))
                    # Ограничиваем количество рассматриваемых вариантов размещения
                    # (например, первые MAX_PERMUTATIONS_STREET_N по какой-то сортировке или случайные)
                    for slot_perm in slot_permutations[:MAX_PERMUTATIONS_STREET_N]: # Используем константу
                        placements = []
                        current_board_copy = self.board.copy()
                        valid_placement = True
                        # Размещаем первую карту
                        placements.append((cards_to_place_val[0], slot_perm[0][0], slot_perm[0][1]))
                        current_board_copy.add_card(cards_to_place_val[0], slot_perm[0][0], slot_perm[0][1])
                        # Размещаем вторую карту
                        placements.append((cards_to_place_val[1], slot_perm[1][0], slot_perm[1][1]))
                        current_board_copy.add_card(cards_to_place_val[1], slot_perm[1][0], slot_perm[1][1])

                        # Простое правило: не ставить трипс на топ на первой улице (здесь не первая улица)
                        # Немедленная проверка на фол не нужна здесь, т.к. MCTS это оценит.
                        # Но можно отсеять совсем плохие варианты.

                        p_info = {'placements': placements, 'discarded': card_to_discard_val}
                        possible_placements_infos.append(p_info)
                else: # Недостаточно слотов для размещения 2 карт
                    pass


        # Создаем узлы на основе сгенерированных placement_infos
        for p_info_dict in possible_placements_infos:
            new_board_state = self.board.copy()
            current_placements = p_info_dict['placements']
            discarded_card_result = p_info_dict['discarded']

            try:
                for card_int, row_name, slot_idx in current_placements:
                    new_board_state.add_card(card_int, row_name, slot_idx)

                # Ключ для children и _generated_states_for_expand
                # Должен быть уникальным для каждого действия (размещения + сброса)
                # Состоит из кортежа размещений (карта, ряд, индекс) и сброшенной карты
                placement_tuples = tuple(sorted([(int(p[0]), str(p[1]), int(p[2])) for p in current_placements]))
                action_key = (placement_tuples, discarded_card_result) # Включаем сброс в ключ

                generated_states.append((new_board_state, discarded_card_result))
                self._generated_states_for_expand[action_key] = (new_board_state, discarded_card_result, p_info_dict)

            except ValueError as ve: # Например, попытка положить карту в занятый слот (не должно быть при правильной генерации)
                logger.warning(f"Invalid placement during state generation: {ve} for {p_info_dict}")
            except Exception as e:
                logger.error(f"Unexpected error during state generation: {e} for {p_info_dict}", exc_info=True)

        # Ограничиваем количество состояний, если их слишком много (например, для первой улицы)
        # Это должно быть сделано на этапе генерации `possible_placements_infos`
        # logger.debug(f"Generated {len(generated_states)} next states for node with {self.board.get_total_cards()} cards, dealt {len(cards_just_dealt)}.")
        return generated_states


    def expand(self) -> Optional['MCTSNode']:
        """ Расширяет текущий узел, добавляя одного нового ребенка. """
        if not self.untried_next_states:
            # logger.debug(f"Expand called on node with no untried states. Board cards: {self.board.get_total_cards()}")
            # Если untried_next_states пуст, но _generated_states_for_expand не пуст,
            # это значит, что все сгенерированные состояния уже были развернуты в детей.
            if not self._generated_states_for_expand or all(key in self.children for key in self._generated_states_for_expand):
                 # logger.debug("All generated states already expanded or no states to expand.")
                 return None # Нечего расширять

        # Ищем ключ в _generated_states_for_expand, которого еще нет в self.children
        next_action_key_to_expand: Optional[Tuple] = None
        for key_candidate in self._generated_states_for_expand.keys():
            if key_candidate not in self.children:
                next_action_key_to_expand = key_candidate
                break
        
        if next_action_key_to_expand is None:
            # logger.debug("No unexpanded action keys found in _generated_states_for_expand.")
            return None

        board_state, discarded_card, placement_info_for_child = self._generated_states_for_expand[next_action_key_to_expand]

        new_deck = self.remaining_deck.copy()
        if placement_info_for_child:
            for card_int, _, _ in placement_info_for_child.get('placements', []):
                new_deck.discard(card_int)
            if placement_info_for_child.get('discarded') is not None:
                new_deck.discard(placement_info_for_child['discarded'])
        
        child_node = MCTSNode(board_state, new_deck, parent=self, placement_info=placement_info_for_child)
        self.children[next_action_key_to_expand] = child_node
        # logger.debug(f"Expanded child for action key: {next_action_key_to_expand}. Children count: {len(self.children)}")
        return child_node


    def uct_select_child(self, exploration_constant: float) -> Optional['MCTSNode']:
        """ Выбирает дочерний узел с использованием UCB1-RAVE формулы. """
        if not self.children: return None

        best_score = -float('inf')
        best_children: List[MCTSNode] = []

        for child_key, child in self.children.items():
            if child.visits == 0: # Если есть неисследованные дети, выбираем одного из них
                # logger.debug(f"Child {child_key} has 0 visits, selecting for rollout.")
                return child # Всегда исследуем неисследованные узлы в первую очередь (стандартный MCTS)

            # UCB1 part
            ucb_score = child.total_reward / child.visits + \
                        exploration_constant * math.sqrt(math.log(self.visits) / child.visits)
            
            # RAVE part
            rave_score = child.rave_reward / child.rave_visits if child.rave_visits > 0 else 0.0
            alpha = max(0.0, (RAVE_K - self.visits) / RAVE_K) # RAVE_K - параметр
            # alpha = RAVE_K / (RAVE_K + 3 * self.visits) # Другой вариант alpha

            score = (1 - alpha) * ucb_score + alpha * rave_score
            # logger.debug(f"Child {child_key}: V={child.visits}, R={child.total_reward:.2f}, RV={child.rave_visits}, RR={child.rave_reward:.2f}, Alpha={alpha:.2f}, UCB={ucb_score:.2f}, RAVE_S={rave_score:.2f} -> Score={score:.2f}")

            if score > best_score:
                best_score = score
                best_children = [child]
            elif score == best_score:
                best_children.append(child)
        
        return random.choice(best_children) if best_children else None

    @staticmethod
    def _calculate_heuristic_score_v2(board: PlayerBoard, deck_snapshot: Set[int]) -> float:
        """
        Улучшенная эвристическая оценка текущей доски.
        Учитывает роялти, фолы, потенциал на Фантазию, "живые" карты.
        """
        if check_board_foul(board): return -1000.0  # Очень большой штраф за фол

        score = 0.0
        total_royalty = calculate_total_royalty_for_board(board)
        score += total_royalty * 2.0 # Роялти важны

        # Оценка Фантазии
        top_cards = board.get_row_cards('top')
        if len(top_cards) == 3:
            try:
                _, type_str, _ = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                if type_str == "Pair":
                    pair_rank = Card.get_rank_int(top_cards[0] if Card.get_rank_int(top_cards[0]) == Card.get_rank_int(top_cards[1]) else top_cards[2]) # Упрощенно
                    if pair_rank >= RANK_MAP['Q']: score += 25 # Бонус за QQ+ на топе (Фантазия)
                elif type_str == "Trips": score += 35 # Большой бонус за трипс на топе (Фантазия)
            except ValueError: pass # Невалидные карты или неполная рука

        # Эвристика "живых" карт для улучшения рук (очень упрощенно)
        # Можно добавить оценку потенциала каждой линии на улучшение
        # Например, для дро-рук (флеш-дро, стрит-дро)
        # Это сложная часть, требующая более глубокого анализа

        # Пример: бонус за почти готовые сильные руки
        mid_cards = board.get_row_cards('middle')
        bot_cards = board.get_row_cards('bottom')

        if 4 <= len(mid_cards) < 5 : score += MCTSNode._estimate_draw_potential(mid_cards, deck_snapshot) * 0.5
        if 4 <= len(bot_cards) < 5 : score += MCTSNode._estimate_draw_potential(bot_cards, deck_snapshot)

        # Штраф за "мертвые" карты на верхнем ряду, если они не часть пары/трипса
        if len(top_cards) == 3:
            is_pair_or_trips = False
            try:
                _, type_str, _ = evaluate_3_card_ofc(top_cards[0], top_cards[1], top_cards[2])
                if type_str in ["Pair", "Trips"]: is_pair_or_trips = True
            except ValueError: pass
            if not is_pair_or_trips:
                # Штрафуем за высокие карты на топе, если они не образуют хотя бы пару
                # (чтобы не блокировать хорошие карты для мид/бот)
                for card_int in top_cards:
                    if Card.get_rank_int(card_int) > RANK_MAP['9']: score -= 0.5

        return score

    @staticmethod
    def _estimate_draw_potential(current_cards: List[int], deck: Set[int]) -> float:
        """ Очень упрощенная оценка потенциала дро-руки. """
        potential = 0.0
        if not current_cards or len(current_cards) < 3: return 0.0 # Нужно хотя бы 3-4 карты для оценки дро

        # Потенциал на флеш
        suits = Counter(Card.get_suit_int(c) for c in current_cards)
        for suit_val, count in suits.items():
            if count == 4: # Флеш-дро
                outs = 0
                for card_in_deck in deck:
                    if Card.get_suit_int(card_in_deck) == suit_val: outs +=1
                potential += outs * 0.2 # Примерный вес аута на флеш
            elif count == 3 and len(current_cards) == 3: # Бэкдор флеш-дро (3 карты)
                outs = 0
                for card_in_deck in deck:
                    if Card.get_suit_int(card_in_deck) == suit_val: outs +=1
                if outs >=2 : potential += 1.0 # Небольшой бонус за бэкдор

        # Потенциал на стрит (очень грубо)
        # ... (логика для стрит-дро сложнее, пока пропустим для простоты)
        return potential

    @staticmethod
    def _choose_best_heuristic_placement_v2(
        current_board: PlayerBoard,
        cards_to_act_on: List[int],
        current_deck: Set[int]
    ) -> Optional[Dict[str, Any]]:
        """
        Выбирает лучшее размещение на основе эвристик (улучшенная версия).
        Возвращает placement_info дикт или None.
        """
        import itertools # Локальный импорт

        best_placement_info: Optional[Dict[str, Any]] = None
        best_heuristic_score = -float('inf')

        num_on_board = current_board.get_total_cards()
        num_dealt = len(cards_to_act_on)
        available_slots = current_board.get_available_slots()

        # Определяем, сколько карт разместить и сколько сбросить
        if num_on_board == 0: # Первая улица
            if num_dealt != 5: return None # Некорректно
            cards_to_place_options = [cards_to_act_on]
            cards_to_discard_options = [None]
            num_to_place_on_board = 5
            current_max_perms = MAX_PERMUTATIONS_STREET_1
        elif num_dealt == 3: # Улицы 2-5
            cards_to_place_options = []
            cards_to_discard_options = []
            for i in range(3): # Перебираем карту для сброса
                discard = cards_to_act_on[i]
                place = [cards_to_act_on[j] for j in range(3) if j != i]
                cards_to_place_options.append(place)
                cards_to_discard_options.append(discard)
            num_to_place_on_board = 2
            current_max_perms = MAX_PERMUTATIONS_STREET_N
        else:
            logger.warning(f"Heuristic: Unexpected num_dealt {num_dealt} for board size {num_on_board}")
            return None

        if len(available_slots) < num_to_place_on_board:
            # logger.warning(f"Heuristic: Not enough slots ({len(available_slots)}) to place {num_to_place_on_board} cards.")
            return None

        candidate_actions = []

        for i in range(len(cards_to_place_options)):
            current_cards_to_place = cards_to_place_options[i]
            current_discard = cards_to_discard_options[i]

            # Генерируем перестановки карт для размещения
            for p_cards in itertools.permutations(current_cards_to_place):
                # Генерируем перестановки слотов для размещения
                # Ограничиваем количество перестановок слотов для производительности
                limited_slot_perms = list(itertools.permutations(available_slots, num_to_place_on_board))
                if len(limited_slot_perms) > current_max_perms: # Используем константу
                    limited_slot_perms = random.sample(limited_slot_perms, current_max_perms)

                for p_slots in limited_slot_perms:
                    temp_board = current_board.copy()
                    placements_list = []
                    valid_action = True
                    try:
                        for card_idx in range(num_to_place_on_board):
                            card_val = p_cards[card_idx]
                            row_val, slot_idx_val = p_slots[card_idx]
                            temp_board.add_card(card_val, row_val, slot_idx_val)
                            placements_list.append((card_val, row_val, slot_idx_val))
                        
                        # Правило: не ставить трипс на топ на первой улице
                        if num_on_board == 0 and num_to_place_on_board == 5:
                            ranks_in_hand = Counter(Card.get_rank_int(c) for c in p_cards)
                            trip_rank_in_hand = next((r for r,c in ranks_in_hand.items() if c >=3), -1)
                            if trip_rank_in_hand != -1:
                                for placed_card_val, placed_row, _ in placements_list:
                                    if placed_row == 'top' and Card.get_rank_int(placed_card_val) == trip_rank_in_hand:
                                        valid_action = False; break
                            if not valid_action: continue

                        # Оцениваем это размещение
                        # Используем копию колоды без только что размещенных/сброшенных карт
                        deck_after_action = current_deck.copy()
                        for card_val, _, _ in placements_list: deck_after_action.discard(card_val)
                        if current_discard is not None: deck_after_action.discard(current_discard)
                        
                        heuristic_score = MCTSNode._calculate_heuristic_score_v2(temp_board, deck_after_action)
                        candidate_actions.append({
                            'score': heuristic_score,
                            'placements': placements_list,
                            'discarded': current_discard
                        })
                    except ValueError: # Ошибка добавления карты (например, дубликат - не должно быть)
                        continue # Пропускаем невалидное действие
        
        if not candidate_actions: return None
        
        # Сортируем по убыванию эвристической оценки
        candidate_actions.sort(key=lambda x: x['score'], reverse=True)
        
        # Возвращаем лучший (первый после сортировки)
        best_placement_info = {
            'placements': candidate_actions[0]['placements'],
            'discarded': candidate_actions[0]['discarded']
        }
        # logger.debug(f"Heuristic choice: Score={candidate_actions[0]['score']:.2f}, Placed: {hand_to_str([p[0] for p in best_placement_info['placements']])}, Discarded: {card_to_str(best_placement_info['discarded']) if best_placement_info['discarded'] else 'None'}")
        return best_placement_info


def heuristic_rollout_simulation_v2(board_dict: Dict, deck_list: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Симуляция с использованием улучшенной эвристики для выбора ходов.
    board_dict: состояние доски в виде словаря.
    deck_list: список оставшихся карт в колоде.
    Возвращает (итоговая_награда, список_предпринятых_действий_placement_info).
    """
    current_board = PlayerBoard() # Восстанавливаем объект PlayerBoard
    for r, cards_str_list in board_dict.get('rows', {}).items():
        for i, card_str_val in enumerate(cards_str_list):
            if card_str_val and card_str_val != PlayerBoard.CARD_PLACEHOLDER:
                try: current_board.add_card(Card.from_str(card_str_val), r, i)
                except ValueError: pass # Пропускаем невалидные карты при восстановлении
    
    deck_sim = Deck(cards=deck_list[:]) # Копия колоды для симуляции
    random.shuffle(deck_sim.cards) # Перемешиваем колоду для симуляции
    
    simulation_actions_taken: List[Dict[str, Any]] = []

    try:
        while not current_board.is_complete():
            num_on_board = current_board.get_total_cards()
            if num_on_board == 0: num_to_deal = 5
            elif num_on_board < PlayerBoard.TOTAL_CAPACITY: num_to_deal = 3
            else: break # Доска полна

            if len(deck_sim.cards) < num_to_deal: break # Недостаточно карт в колоде
            
            dealt_cards = deck_sim.deal(num_to_deal)
            if not dealt_cards: break # Не удалось сдать карты

            # Используем _choose_best_heuristic_placement_v2 для выбора хода
            deck_sim_set = set(deck_sim.get_cards())
            best_action = MCTSNode._choose_best_heuristic_placement_v2(current_board, dealt_cards, deck_sim_set)

            if best_action and best_action.get('placements'):
                action_placements = cast(List[Tuple[int, str, int]], best_action['placements'])
                action_discarded = cast(Optional[int], best_action.get('discarded'))
                
                valid_move = True
                for card_int, row, slot_idx in action_placements:
                    try: current_board.add_card(card_int, row, slot_idx)
                    except ValueError: valid_move = False; break # Ошибка размещения
                if not valid_move: break # Прерываем симуляцию при ошибке

                simulation_actions_taken.append(best_action)
                # Карты из dealt_cards, которые не были размещены или сброшены,
                # теоретически должны вернуться в колоду симуляции, но эвристика должна использовать все.
            else: # Эвристика не смогла выбрать ход, прерываем
                # logger.warning("Heuristic rollout: No best action found. Breaking.")
                break
        
        # Оцениваем финальное состояние доски
        if check_board_foul(current_board): final_reward = -10.0 # Штраф за фол
        else: final_reward = float(calculate_total_royalty_for_board(current_board))

    except Exception as e:
        logger.error(f"Error during heuristic rollout simulation v2.8.3: {e}", exc_info=True)
        final_reward = -20.0 # Штраф за ошибку в симуляции
    
    return final_reward, simulation_actions_taken


# Функция для параллельного запуска (остается как есть, если не требует изменений)
def run_parallel_rollout(board_dict: Dict, deck_list: List[int]) -> Tuple[float, List[Dict[str, Any]]]:
    # Используем улучшенную эвристическую симуляцию
    return heuristic_rollout_simulation_v2(board_dict, deck_list)
