# ofc_evaluator_5card.py v1.9 (DEBUG VERSION)
"""
Оценка 5-карточной руки OFC + генерация таблиц поиска.
Исправлено определение WORST_RANK_5CARD.
Добавлено детальное логгирование в evaluate для отладки RF и других рук.
Убедились в наличии логов для prime_product и rank_bitmask.
!!! ДОБАВЛЕНЫ ОТЛАДОЧНЫЕ ЛОГИ В evaluate !!!
"""
import itertools
import traceback
import sys
import logging
from typing import Dict, List, Generator, Optional, Tuple
from collections import Counter

# Импортируем Card и PRIMES из ofc_logic
try:
    from ofc_logic import Card, PRIMES, INT_RANKS, INVALID_CARD, card_to_str
except ImportError:
    # Заглушки для возможности анализа
    class Card: # type: ignore
        PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41]
        INT_RANKS = range(13)
        @staticmethod
        def get_prime(c): return 1
        @staticmethod
        def get_rank_int(c): return 0
        @staticmethod
        def prime_product_from_rankbits(rankbits): return 1
        @staticmethod
        def prime_product_from_hand(card_ints): return 1
        @staticmethod
        def to_str(c): return "??"
    PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41]
    INT_RANKS = range(13)
    INVALID_CARD = -1
    def card_to_str(c): return Card.to_str(c)
    logging.error("Could not import from ofc_logic in ofc_evaluator_5card.py")

# Получаем логгер
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    # Настраиваем логгер, если он еще не настроен
    logger.setLevel(logging.DEBUG) # Устанавливаем DEBUG для отладки
    handler = logging.StreamHandler() # Вывод в консоль
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- Класс LookupTable5Card ---
class LookupTable5Card:
    """Создает и хранит таблицы поиска для 5-карточных рук."""
    MAX_STRAIGHT_FLUSH: int = 10
    MAX_FOUR_OF_A_KIND: int = 166
    MAX_FULL_HOUSE: int = 322
    MAX_FLUSH: int = 1599
    MAX_STRAIGHT: int = 1609
    MAX_THREE_OF_A_KIND: int = 2467
    MAX_TWO_PAIR: int = 3325
    MAX_PAIR: int = 6185
    MAX_HIGH_CARD: int = 7462 # Худший *валидный* ранг

    # WORST_RANK_5CARD - это ранг для ошибок, он должен быть хуже MAX_HIGH_CARD
    WORST_RANK_5CARD: int = MAX_HIGH_CARD + 1 # 7463

    RANK_CLASS_TO_STRING: Dict[int, str] = {
        1: "Straight Flush", 2: "Four of a Kind", 3: "Full House", 4: "Flush",
        5: "Straight", 6: "Three of a Kind", 7: "Two Pair", 8: "Pair", 9: "High Card"
    }

    def __init__(self):
        """Инициализирует и вычисляет таблицы поиска."""
        self.flush_lookup: Dict[int, int] = {}
        self.unsuited_lookup: Dict[int, int] = {}
        logger.info("Initializing 5-card lookup tables...")
        try:
            self._calculate_flushes()
            self._calculate_multiples()
            logger.info(f"5-card tables initialized. Flush: {len(self.flush_lookup)}, Unsuited: {len(self.unsuited_lookup)}")
            # Дополнительная проверка наличия ключа RF
            rf_bits = 0b1111100000000
            rf_prime = self._prime_product_from_rankbits(rf_bits)
            if rf_prime not in self.flush_lookup:
                 logger.error(f"FATAL: Royal Flush prime product {rf_prime} not found in generated flush_lookup!")
            else:
                 logger.debug(f"Royal Flush prime product {rf_prime} found with rank {self.flush_lookup[rf_prime]}.")

        except Exception as e:
             logger.critical(f"Error during 5-card lookup table calculation: {e}", exc_info=True)
             raise

    def _calculate_flushes(self):
        """Вычисляет ранги для стрит-флешей и обычных флешей."""
        # Биты для стрит-флешей (от AKQJT до A5432)
        straight_flushes_rank_bits: List[int] = [
            0b1111100000000, # AKQJT (Royal)
            0b0111110000000, # KQJT9
            0b0011111000000, # QJT98
            0b0001111100000, # JT987
            0b0000111110000, # T9876
            0b0000011111000, # 98765
            0b0000001111100, # 87654
            0b0000000111110, # 76543
            0b0000000011111, # 65432
            0b1000000001111, # A5432 (Wheel)
        ]
        # Генерация всех возможных 5-битных комбинаций из 13 рангов
        all_flush_rank_bits: List[int] = []
        # Используем itertools.combinations для надежности
        for combo_indices in itertools.combinations(INT_RANKS, 5):
            bits = 0
            for index in combo_indices:
                bits |= (1 << index)
            all_flush_rank_bits.append(bits)

        straight_flush_set = set(straight_flushes_rank_bits)
        # Итерация normal_flush_rank_bits в обратном порядке для правильного ранжирования
        # Сортируем по убыванию битовой маски (что соответствует убыванию силы руки)
        normal_flush_rank_bits = sorted(
            [rb for rb in all_flush_rank_bits if rb not in straight_flush_set],
            reverse=True
        )

        # Ранжируем стрит-флеши (1-10)
        rank = 1
        # Итерируем в порядке убывания силы (как в списке straight_flushes_rank_bits)
        for sf_bits in straight_flushes_rank_bits:
            prime_product = self._prime_product_from_rankbits(sf_bits)
            if prime_product == 0: # Добавим проверку на нулевой продукт
                 logger.error(f"Zero prime product for SF bits {bin(sf_bits)}!")
                 continue
            self.flush_lookup[prime_product] = rank
            # Логгирование для RF
            if rank == 1: logger.debug(f"Generated RF entry: prime={prime_product}, rank={rank}")
            rank += 1
        if rank -1 != self.MAX_STRAIGHT_FLUSH:
            logger.error(f"SF ranks mismatch: Expected {self.MAX_STRAIGHT_FLUSH}, got {rank-1}")

        # Ранжируем обычные флеши (323 - 1599)
        rank = self.MAX_FULL_HOUSE + 1 # Начинаем с 323
        for f_bits in normal_flush_rank_bits: # Итерация от старших к младшим
            prime_product = self._prime_product_from_rankbits(f_bits)
            if prime_product == 0:
                 logger.error(f"Zero prime product for Flush bits {bin(f_bits)}!")
                 continue
            self.flush_lookup[prime_product] = rank
            rank += 1
        if rank -1 != self.MAX_FLUSH:
            logger.warning(f"Flush ranks mismatch: Expected {self.MAX_FLUSH}, got {rank-1}")

        # Передаем биты для стритов и старших карт в другую функцию
        self._calculate_straights_and_highcards(straight_flushes_rank_bits, normal_flush_rank_bits)

    def _calculate_straights_and_highcards(self, straights_rank_bits: List[int], highcards_rank_bits: List[int]):
        """Вычисляет ранги для стритов и старших карт (не флеш)."""
        # Ранжируем стриты (1600 - 1609)
        rank = self.MAX_FLUSH + 1 # Начинаем с 1600
        for s_bits in straights_rank_bits: # Итерация от старших к младшим (как в straight_flushes_rank_bits)
            prime_product = self._prime_product_from_rankbits(s_bits)
            if prime_product == 0:
                 logger.error(f"Zero prime product for Straight bits {bin(s_bits)}!")
                 continue
            self.unsuited_lookup[prime_product] = rank
            rank += 1
        if rank -1 != self.MAX_STRAIGHT:
            logger.warning(f"Straight ranks mismatch: Expected {self.MAX_STRAIGHT}, got {rank-1}")

        # Ранжируем старшие карты (6186 - 7462)
        rank = self.MAX_PAIR + 1 # Начинаем с 6186
        for h_bits in highcards_rank_bits: # Итерация от старших к младшим
            prime_product = self._prime_product_from_rankbits(h_bits)
            if prime_product == 0:
                 logger.error(f"Zero prime product for High Card bits {bin(h_bits)}!")
                 continue
            self.unsuited_lookup[prime_product] = rank
            rank += 1
        if rank -1 != self.MAX_HIGH_CARD:
            logger.warning(f"High card ranks mismatch: Expected {self.MAX_HIGH_CARD}, got {rank-1}")

    def _calculate_multiples(self):
        """Вычисляет ранги для Каре, Фулл-хаусов, Сетов, Двух пар и Пар."""
        backwards_ranks = range(len(INT_RANKS) - 1, -1, -1) # От A до 2

        # Каре (11 - 166)
        rank = self.MAX_STRAIGHT_FLUSH + 1 # Начинаем с 11
        for quad_idx in backwards_ranks: # От A до 2
            # Кикеры от старшего к младшему
            kickers = sorted([k for k in backwards_ranks if k != quad_idx], reverse=True)
            for kick_idx in kickers:
                prod = PRIMES[quad_idx]**4 * PRIMES[kick_idx]
                self.unsuited_lookup[prod] = rank
                rank += 1
        if rank -1 != self.MAX_FOUR_OF_A_KIND:
            logger.warning(f"4oak ranks mismatch: Expected {self.MAX_FOUR_OF_A_KIND}, got {rank-1}")

        # Фулл-хаус (167 - 322)
        rank = self.MAX_FOUR_OF_A_KIND + 1 # Начинаем с 167
        for trip_idx in backwards_ranks: # От A до 2
            # Пары от старшей к младшей
            pairs = sorted([p for p in backwards_ranks if p != trip_idx], reverse=True)
            for pair_idx in pairs:
                prod = PRIMES[trip_idx]**3 * PRIMES[pair_idx]**2
                self.unsuited_lookup[prod] = rank
                rank += 1
        if rank -1 != self.MAX_FULL_HOUSE:
            logger.warning(f"FH ranks mismatch: Expected {self.MAX_FULL_HOUSE}, got {rank-1}")

        # Сет (1610 - 2467)
        rank = self.MAX_STRAIGHT + 1 # Начинаем с 1610
        for trip_idx in backwards_ranks: # От A до 2
            kickers = [k for k in backwards_ranks if k != trip_idx]
            # Комбинации кикеров от старших к младшим
            kicker_combos = sorted(itertools.combinations(kickers, 2), reverse=True)
            for k1, k2 in kicker_combos:
                prod = PRIMES[trip_idx]**3 * PRIMES[k1] * PRIMES[k2]
                self.unsuited_lookup[prod] = rank
                rank += 1
        if rank -1 != self.MAX_THREE_OF_A_KIND:
            logger.warning(f"3oak ranks mismatch: Expected {self.MAX_THREE_OF_A_KIND}, got {rank-1}")

        # Две пары (2468 - 3325)
        rank = self.MAX_THREE_OF_A_KIND + 1 # Начинаем с 2468
        # Комбинации пар от старших к младшим
        pair_combos = sorted(itertools.combinations(backwards_ranks, 2), reverse=True)
        for p1, p2 in pair_combos:
            # Кикеры от старшего к младшему
            kickers = sorted([k for k in backwards_ranks if k != p1 and k != p2], reverse=True)
            for kick_idx in kickers:
                prod = PRIMES[p1]**2 * PRIMES[p2]**2 * PRIMES[kick_idx]
                self.unsuited_lookup[prod] = rank
                rank += 1
        if rank -1 != self.MAX_TWO_PAIR:
            logger.warning(f"2pair ranks mismatch: Expected {self.MAX_TWO_PAIR}, got {rank-1}")

        # Пара (3326 - 6185)
        rank = self.MAX_TWO_PAIR + 1 # Начинаем с 3326
        for pair_idx in backwards_ranks: # От A до 2
            kickers = [k for k in backwards_ranks if k != pair_idx]
            # Комбинации кикеров от старших к младшим
            kicker_combos = sorted(itertools.combinations(kickers, 3), reverse=True)
            for k1, k2, k3 in kicker_combos:
                prod = PRIMES[pair_idx]**2 * PRIMES[k1] * PRIMES[k2] * PRIMES[k3]
                self.unsuited_lookup[prod] = rank
                rank += 1
        if rank -1 != self.MAX_PAIR:
            logger.warning(f"Pair ranks mismatch: Expected {self.MAX_PAIR}, got {rank-1}")

    def _get_lexographically_next_bit_sequence(self, bits: int) -> Generator[int, None, None]:
        """Генератор следующей лексикографической перестановки битов (Gosper's Hack)."""
        # Эта функция больше не используется, так как генерация заменена на itertools
        # Оставляем на всякий случай, но она не вызывается
        next_val = bits
        while True:
            if next_val == 0: break
            try:
                rightmost_one = next_val & -next_val; next_higher_one_bit = next_val + rightmost_one
                rightmost_block_of_ones = next_val ^ next_higher_one_bit
                rightmost_block_shifted = (rightmost_block_of_ones // rightmost_one) >> 2
                next_val = next_higher_one_bit | rightmost_block_shifted
            except Exception as e_gosper: logger.error(f"Error in Gosper's Hack: {e_gosper}", exc_info=True); break
            # Ограничение, чтобы не выйти за пределы 13 бит для рангов
            if next_val >= (1 << 13): break
            yield next_val

    def _prime_product_from_rankbits(self, rankbits: int) -> int:
        """Вычисляет произведение простых чисел для рангов из битовой маски."""
        product = 1
        for i in INT_RANKS:
            if rankbits & (1 << i):
                try:
                    product *= PRIMES[i]
                except IndexError:
                    logger.warning(f"Invalid rank index {i} in prime product calculation for bits {bin(rankbits)}.")
                    return 0 # Возвращаем 0 при ошибке индекса
        return product

# --- Класс Evaluator5Card ---
class Evaluator5Card:
    """Оценивает 5-карточные руки, используя таблицы поиска."""
    def __init__(self):
        """Инициализирует эвалуатор, загружая таблицы."""
        self.table = LookupTable5Card()

    def evaluate(self, cards: List[int]) -> int:
        """
        Оценивает 5-карточную руку.
        Возвращает ранг (1-7462) или WORST_RANK_5CARD (7463) при ошибке.
        """
        hand_str_log = [card_to_str(c) for c in cards] # Для логгирования
        logger.debug(f"Evaluating 5-card hand: {hand_str_log} (ints: {cards})") # Лог входа

        if len(cards) != 5:
            logger.warning(f"Evaluator5Card.evaluate requires 5 cards, got {len(cards)}")
            return self.table.WORST_RANK_5CARD # Возвращаем невалидный ранг

        valid_cards: List[int] = []
        try:
            for c in cards:
                 if not isinstance(c, int) or c == INVALID_CARD or c <= 0:
                     raise ValueError(f"Invalid card integer: {c}")
                 valid_cards.append(c)
            if len(valid_cards) != len(set(valid_cards)):
                raise ValueError(f"Duplicate cards found: {hand_str_log}")
        except ValueError as e:
            logger.warning(f"Invalid input for 5-card evaluation: {e}")
            return self.table.WORST_RANK_5CARD

        # --- ДОБАВЛЕНО ЛОГГИРОВАНИЕ РАНГОВ ---
        ranks_extracted = []
        for card_int in valid_cards:
            rank_int = Card.get_rank_int(card_int)
            ranks_extracted.append(rank_int)
        logger.debug(f"  Extracted ranks: {ranks_extracted}")

        # Проверка на флеш
        suit_mask = valid_cards[0] & valid_cards[1] & valid_cards[2] & valid_cards[3] & valid_cards[4] & 0xF000
        if suit_mask != 0: # Флеш или Стрит-флеш
            logger.debug(f"Hand {hand_str_log} detected as Flush/SF (suit_mask={suit_mask})")
            # Вычисляем битовую маску рангов
            rank_bitmask = 0
            for rank_int in ranks_extracted: # Используем уже извлеченные ранги
                rank_bitmask |= (1 << rank_int)
            logger.debug(f"Rank bitmask: {bin(rank_bitmask)}")

            prime_product = self.table._prime_product_from_rankbits(rank_bitmask)
            logger.debug(f"Calculated prime product (flush): {prime_product}")
            if prime_product == 0: # Ошибка при вычислении произведения
                 logger.error(f"Zero prime product for flush hand: {hand_str_log}")
                 return self.table.WORST_RANK_5CARD

            # Ищем в таблице флешей
            rank = self.table.flush_lookup.get(prime_product)
            logger.debug(f"Lookup result in flush_lookup for {prime_product}: {rank}")

            if rank is None:
                # Логгируем ошибку, если не нашли
                rf_bits = 0b1111100000000
                if rank_bitmask == rf_bits:
                    logger.error(f"FATAL: Royal Flush prime product {prime_product} (bits {bin(rank_bitmask)}) not found in lookup for hand {hand_str_log}!")
                else:
                    logger.warning(f"Flush prime product {prime_product} not found for hand {hand_str_log} (bitmask {bin(rank_bitmask)})")
                return self.table.WORST_RANK_5CARD # Возвращаем невалидный ранг
            logger.debug(f"Returning rank (flush): {rank}") # Лог возврата
            return rank
        else: # Не флеш
            logger.debug(f"Hand {hand_str_log} detected as Unsuited")
            # Используем Counter для правильного ключа
            prime_product = 1
            try:
                # Используем уже извлеченные ранги
                rank_counts = Counter(ranks_extracted)
                logger.debug(f"Rank counts: {rank_counts}")
                for rank_index, count in rank_counts.items():
                    prime_product *= PRIMES[rank_index] ** count
                logger.debug(f"Calculated prime product (unsuited): {prime_product}")
            except Exception as e:
                logger.error(f"Error calculating prime product for unsuited hand {hand_str_log}: {e}")
                return self.table.WORST_RANK_5CARD

            # Ищем в таблице не-флешей
            rank = self.table.unsuited_lookup.get(prime_product)
            logger.debug(f"Lookup result in unsuited_lookup for {prime_product}: {rank}")

            if rank is None:
                logger.warning(f"Unsuited prime product {prime_product} not found for hand {hand_str_log} (ranks: {ranks_extracted})")
                return self.table.WORST_RANK_5CARD # Возвращаем невалидный ранг
            logger.debug(f"Returning rank (unsuited): {rank}") # Лог возврата
            return rank

    def get_rank_class(self, hand_rank: int) -> int:
        """Возвращает класс руки (1-9) по её рангу."""
        # Проверяем на невалидный ранг
        if not isinstance(hand_rank, int) or hand_rank <= 0 or hand_rank >= self.table.WORST_RANK_5CARD:
            logger.debug(f"get_rank_class received invalid rank: {hand_rank}, returning 9")
            return 9 # Возвращаем High Card (худший класс) для невалидных рангов

        # Определяем класс по диапазонам
        if hand_rank <= self.table.MAX_STRAIGHT_FLUSH: return 1
        elif hand_rank <= self.table.MAX_FOUR_OF_A_KIND: return 2
        elif hand_rank <= self.table.MAX_FULL_HOUSE: return 3
        elif hand_rank <= self.table.MAX_FLUSH: return 4
        elif hand_rank <= self.table.MAX_STRAIGHT: return 5
        elif hand_rank <= self.table.MAX_THREE_OF_A_KIND: return 6
        elif hand_rank <= self.table.MAX_TWO_PAIR: return 7
        elif hand_rank <= self.table.MAX_PAIR: return 8
        # Все оставшиеся валидные ранги (до MAX_HIGH_CARD включительно) - это High Card
        elif hand_rank <= self.table.MAX_HIGH_CARD: return 9
        else:
            # Эта ветка не должна достигаться из-за проверки в начале, но оставим на всякий случай
            logger.warning(f"Unexpected hand rank {hand_rank} in get_rank_class.")
            return 9

    def class_to_string(self, class_int: int) -> str:
        """Преобразует целочисленный класс руки в строку."""
        return self.table.RANK_CLASS_TO_STRING.get(class_int, "Unknown")

# Создаем глобальный экземпляр для использования другими модулями
try:
    evaluator_5card_instance = Evaluator5Card()
except Exception as e_global:
    logger.critical(f"Failed to create global Evaluator5Card instance: {e_global}", exc_info=True)
    # Создаем заглушку, чтобы импорт не падал
    class MockEvaluator5Card:
        MAX_HIGH_CARD = 7462
        WORST_RANK_5CARD = MAX_HIGH_CARD + 1
        def evaluate(self, cards): return self.WORST_RANK_5CARD
        def get_rank_class(self, rank): return 9
        def class_to_string(self, r_class): return "Error"
        table = MockEvaluator5Card() # type: ignore
    evaluator_5card_instance = MockEvaluator5Card() # type: ignore
